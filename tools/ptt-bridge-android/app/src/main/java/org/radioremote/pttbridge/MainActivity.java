package org.radioremote.pttbridge;

import android.Manifest;
import android.app.Activity;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;

public class MainActivity extends Activity {
    private TextView statusView;
    private RadioGroup group;
    private Button toggle;
    private final List<String> addrs = new ArrayList<>();
    private final Handler h = new Handler(Looper.getMainLooper());

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        root.setPadding(pad, pad, pad, pad);
        TextView t = new TextView(this);
        t.setTextSize(20); t.setText("Radio Remote PTT");
        TextView info = new TextView(this);
        info.setText("Pair the speaker-mic or PTT button in Android's Bluetooth settings first, choose it below and start. "
            + "Then open Radio Remote in Chrome on this phone, Audio tab, Bind PTT, and press the device's button.");
        statusView = new TextView(this); statusView.setTextSize(16); statusView.setPadding(0, pad, 0, pad);
        group = new RadioGroup(this);
        toggle = new Button(this);
        toggle.setOnClickListener(v -> {
            SharedPreferences sp = getSharedPreferences(BridgeService.PREFS, MODE_PRIVATE);
            if (BridgeService.running) { sp.edit().putBoolean(BridgeService.KEY_AUTO, false).apply(); stopService(new Intent(this, BridgeService.class)); }
            else { sp.edit().putBoolean(BridgeService.KEY_AUTO, true).apply(); startBridge(); }
            h.postDelayed(this::refresh, 300);
        });
        root.addView(t); root.addView(info); root.addView(statusView); root.addView(group); root.addView(toggle);
        ScrollView sv = new ScrollView(this); sv.addView(root);
        setContentView(sv);
    }

    @Override protected void onResume() {
        super.onResume();
        needPermissions();
        listDevices();
        refresh();
    }
    @Override protected void onPause() { super.onPause(); h.removeCallbacksAndMessages(null); }

    private boolean needPermissions() {
        List<String> need = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 31 && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) need.add(Manifest.permission.BLUETOOTH_CONNECT);
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) need.add(Manifest.permission.POST_NOTIFICATIONS);
        if (need.isEmpty()) return true;
        requestPermissions(need.toArray(new String[0]), 1);
        return false;
    }
    @Override public void onRequestPermissionsResult(int code, String[] p, int[] r) { listDevices(); refresh(); }

    private void listDevices() {
        group.removeAllViews(); addrs.clear();
        BluetoothAdapter ad = BluetoothAdapter.getDefaultAdapter();
        SharedPreferences sp = getSharedPreferences(BridgeService.PREFS, MODE_PRIVATE);
        String sel = sp.getString(BridgeService.KEY_ADDR, null);
        try {
            if (ad == null || Build.VERSION.SDK_INT >= 31 && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) return;
            for (BluetoothDevice d : ad.getBondedDevices()) {
                RadioButton rb = new RadioButton(this);
                rb.setText(d.getName() + "  " + d.getAddress());
                rb.setId(View.generateViewId());
                group.addView(rb); addrs.add(d.getAddress());
                if (d.getAddress().equals(sel)) rb.setChecked(true);
            }
        } catch (SecurityException e) { /* asked again after the permission answer */ }
        group.setOnCheckedChangeListener((g, id) -> {
            int i = g.indexOfChild(g.findViewById(id));
            if (i >= 0) sp.edit().putString(BridgeService.KEY_ADDR, addrs.get(i)).apply();
        });
    }

    private void startBridge() {
        if (!needPermissions()) return;
        Intent i = new Intent(this, BridgeService.class);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i); else startService(i);
    }

    private void refresh() {
        statusView.setText((BridgeService.running ? "Running: " : "Stopped. ") + (BridgeService.running ? BridgeService.status : ""));
        toggle.setText(BridgeService.running ? "Stop" : "Start");
        h.removeCallbacksAndMessages(null);
        h.postDelayed(this::refresh, 1000);
    }
}
