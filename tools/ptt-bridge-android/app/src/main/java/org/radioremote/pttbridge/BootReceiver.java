package org.radioremote.pttbridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/** Starts the helper again after the phone restarts, if it was running when the phone was switched off. */
public class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent i) {
        if (!Intent.ACTION_BOOT_COMPLETED.equals(i.getAction())) return;
        if (!c.getSharedPreferences(BridgeService.PREFS, Context.MODE_PRIVATE).getBoolean(BridgeService.KEY_AUTO, false)) return;
        Intent s = new Intent(c, BridgeService.class);
        try { if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(s); else c.startService(s); } catch (Exception e) { /* the system refused: the person starts it by hand */ }
    }
}
