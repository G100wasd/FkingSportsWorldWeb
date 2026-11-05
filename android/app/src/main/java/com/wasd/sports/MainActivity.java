package com.wasd.sports;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.webkit.GeolocationPermissions;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;

/* 纯 WebView 壳：加载电脑端 wasdSports 服务。
 * 首启弹服务器地址输入（持久化），长按页面可重新填写。
 * 后端不在手机本地跑——APK 只负责显示。 */
public class MainActivity extends Activity {
    private static final String PREFS = "cfg", KEY_URL = "server";
    private WebView web;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(
                new String[]{Manifest.permission.ACCESS_FINE_LOCATION}, 1);
        }
        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        String url = sp.getString(KEY_URL, "");
        if (url.isEmpty()) promptUrl(sp); else load(url);
    }

    private void promptUrl(SharedPreferences sp) {
        EditText et = new EditText(this);
        et.setHint("http://192.168.x.x:8000");
        et.setText(sp.getString(KEY_URL, "http://"));
        et.setSelection(et.getText().length());
        new AlertDialog.Builder(this)
            .setTitle("服务器地址")
            .setMessage("电脑端 NekoSportsWorld 的访问地址（同一 WiFi 下的局域网 IP:端口）")
            .setView(et)
            .setCancelable(false)
            .setPositiveButton("连接", (d, w) -> {
                String u = et.getText().toString().trim();
                if (!u.startsWith("http")) u = "http://" + u;
                sp.edit().putString(KEY_URL, u).apply();
                load(u);
            }).show();
    }

    private void load(String url) {
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setGeolocationEnabled(true);
        web.setWebViewClient(new WebViewClient());
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onGeolocationPermissionsShowPrompt(
                    String origin, GeolocationPermissions.Callback cb) {
                cb.invoke(origin, true, false);
            }
        });
        web.setOnLongClickListener(v -> {
            promptUrl(getSharedPreferences(PREFS, MODE_PRIVATE));
            return true;
        });
        setContentView(web);
        web.loadUrl(url);
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }
}
