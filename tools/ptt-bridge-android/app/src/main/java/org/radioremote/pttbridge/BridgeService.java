package org.radioremote.pttbridge;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothSocket;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Base64;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Reads the PTT button of a paired Bluetooth serial device ("+PTT=P" down, "+PTT=R" up) and hands it to the Radio Remote page
 * over ws://127.0.0.1:17991. Same one-way messages as the Mac helper: {"t":"hello","v":1,"connected":b,"device":name}, {"t":"ptt","down":b}.
 */
public class BridgeService extends Service {
    static final int PORT = 17991;
    static final String PREFS = "ptt", KEY_ADDR = "addr", KEY_AUTO = "auto";
    static final UUID SPP = UUID.fromString("00001101-0000-1000-8000-00805f9b34fb");
    static volatile String status = "Not running";
    static volatile boolean running = false;

    private final CopyOnWriteArrayList<Client> clients = new CopyOnWriteArrayList<>();
    private volatile boolean stop = false, connected = false, down = false;
    private volatile String deviceName = "";
    private volatile BluetoothSocket socket;
    private ServerSocket server;

    static final class Client {
        final Socket s; final OutputStream out;
        Client(Socket s) throws IOException { this.s = s; this.out = s.getOutputStream(); }
        synchronized void sendText(String text) throws IOException {
            byte[] p = text.getBytes(StandardCharsets.UTF_8);
            out.write(0x81);
            if (p.length < 126) out.write(p.length);
            else { out.write(126); out.write(p.length >> 8); out.write(p.length & 255); }
            out.write(p); out.flush();
        }
    }

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (running) return START_STICKY;
        running = true; stop = false;
        startForegroundNow();
        new Thread(this::serve, "ws").start();
        new Thread(this::bluetoothLoop, "bt").start();
        return START_STICKY;
    }

    @Override public void onDestroy() {
        stop = true; running = false; status = "Not running";
        try { server.close(); } catch (Exception e) { /* closed */ }
        try { socket.close(); } catch (Exception e) { /* closed */ }
        for (Client c : clients) try { c.s.close(); } catch (IOException e) { /* closed */ }
    }

    private void startForegroundNow() {
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26) nm.createNotificationChannel(new NotificationChannel("ptt", "PTT button", NotificationManager.IMPORTANCE_LOW));
        PendingIntent pi = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, "ptt") : new Notification.Builder(this);
        Notification n = b.setContentTitle("Radio Remote PTT").setContentText("Passing the Bluetooth PTT button to the page")
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth).setContentIntent(pi).setOngoing(true).build();
        if (Build.VERSION.SDK_INT >= 29) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
        else startForeground(1, n);
    }

    private String hello() {
        return "{\"t\":\"hello\",\"v\":1,\"connected\":" + connected + ",\"device\":\"" + deviceName.replace("\\", "").replace("\"", "") + "\"}";
    }
    private void broadcast(String text) {
        for (Client c : clients) { try { c.sendText(text); } catch (IOException e) { clients.remove(c); try { c.s.close(); } catch (IOException x) { /* closed */ } } }
    }
    private void setDevice(String name) {
        boolean now = name != null;
        if (!now && down) { down = false; broadcast("{\"t\":\"ptt\",\"down\":false}"); }       // never leave the radio keyed when the device goes away
        connected = now; deviceName = now ? name : "";
        broadcast(hello());
    }
    private void ptt(boolean d) { if (d == down) return; down = d; broadcast("{\"t\":\"ptt\",\"down\":" + d + "}"); }

    // ---- local WebSocket server (this phone only) ----
    private void serve() {
        try {
            server = new ServerSocket(PORT, 8, InetAddress.getByName("127.0.0.1"));
            server.setReuseAddress(true);
            while (!stop) { Socket s = server.accept(); new Thread(() -> handle(s), "client").start(); }
        } catch (IOException e) { if (!stop) status = "Cannot listen on port " + PORT + ": " + e.getMessage(); }
    }
    private void handle(Socket s) {
        Client c = null;
        try {
            InputStream in = s.getInputStream();
            StringBuilder head = new StringBuilder(); int b, n = 0;
            while ((b = in.read()) >= 0 && n++ < 8192) { head.append((char) b); if (head.length() >= 4 && head.substring(head.length() - 4).equals("\r\n\r\n")) break; }
            String key = null;
            for (String line : head.toString().split("\r\n")) if (line.toLowerCase().startsWith("sec-websocket-key:")) key = line.substring(18).trim();
            if (key == null) { s.close(); return; }
            String acc = Base64.getEncoder().encodeToString(MessageDigest.getInstance("SHA-1").digest((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").getBytes(StandardCharsets.UTF_8)));
            s.getOutputStream().write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + acc + "\r\n\r\n").getBytes(StandardCharsets.UTF_8));
            c = new Client(s); clients.add(c); c.sendText(hello());
            while (in.read() >= 0) { /* nothing a page sends is acted on; reading notices the close */ }
        } catch (Exception e) { /* gone */ }
        if (c != null) clients.remove(c);
        try { s.close(); } catch (IOException e) { /* closed */ }
    }

    // ---- Bluetooth side ----
    private void bluetoothLoop() {
        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        while (!stop) {
            String addr = sp.getString(KEY_ADDR, null);
            BluetoothAdapter ad = BluetoothAdapter.getDefaultAdapter();
            if (addr == null || ad == null || !ad.isEnabled()) { status = addr == null ? "Choose a device" : "Bluetooth is off"; pause(2000); continue; }
            BluetoothDevice dev = ad.getRemoteDevice(addr);
            String name; try { name = dev.getName(); } catch (SecurityException e) { status = "Bluetooth permission missing"; pause(2000); continue; }
            if (name == null) name = addr;
            BluetoothSocket sock = null;
            try {
                status = "Connecting to " + name + "...";
                try { ad.cancelDiscovery(); } catch (SecurityException e) { /* optional */ }
                try { sock = dev.createRfcommSocketToServiceRecord(SPP); sock.connect(); }
                catch (IOException first) { try { sock.close(); } catch (Exception x) { /* closed */ } sock = dev.createInsecureRfcommSocketToServiceRecord(SPP); sock.connect(); }
                socket = sock;
                status = "Connected to " + name; setDevice(name);
                InputStream in = sock.getInputStream(); byte[] buf = new byte[256]; StringBuilder acc = new StringBuilder(); int n;
                while ((n = in.read(buf)) > 0) {
                    for (int i = 0; i < n; i++) acc.append((char) (buf[i] & 0x7f));
                    for (;;) {                                                           // tokens can arrive split, repeated or glued together
                        int p = acc.indexOf("+PTT=P"), r = acc.indexOf("+PTT=R");
                        if (p < 0 && r < 0) break;
                        boolean isP = r < 0 || (p >= 0 && p < r);
                        acc.delete(0, (isP ? p : r) + 6); ptt(isP);
                    }
                    if (acc.length() > 16) acc.delete(0, acc.length() - 8);
                }
            } catch (Exception e) {
                status = "Waiting for " + name + " (" + (e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage()) + ")";
            }
            if (connected) setDevice(null);
            try { if (sock != null) sock.close(); } catch (IOException e) { /* closed */ }
            pause(3000);
        }
    }
    private void pause(long ms) { try { Thread.sleep(ms); } catch (InterruptedException e) { /* stopping */ } }
}
