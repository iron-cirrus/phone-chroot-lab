package fr.pilote.webui;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/** Ouvre Open WebUI (127.0.0.1:3000 sur le telephone) en plein ecran, comme une application. */
public class WebActivity extends Activity {
    static final String URL = "http://127.0.0.1:3000/";
    static final int BG = 0xFF0E1116;
    final Handler ui = new Handler(Looper.getMainLooper());
    WebView web;
    ValueCallback<Uri[]> chooser;
    boolean alive = true;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);
        web = new WebView(this);
        web.setBackgroundColor(BG);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(true);
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams p) {
                if (chooser != null) chooser.onReceiveValue(null);
                chooser = cb;
                try {
                    startActivityForResult(p.createIntent(), 1);
                } catch (Exception e) { chooser = null; cb.onReceiveValue(null); return false; }
                return true;
            }
        });
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, String url) {
                Uri u = Uri.parse(url);
                String h = u.getHost();
                if ("127.0.0.1".equals(h) || "localhost".equals(h)) return false;
                startActivity(new Intent(Intent.ACTION_VIEW, u));
                return true;
            }
            @Override
            public void onReceivedError(WebView v, WebResourceRequest req, WebResourceError err) {
                if (!req.isForMainFrame()) return;
                v.loadDataWithBaseURL(null,
                    "<body style='background:#0e1116;color:#8b96a5;font:15px sans-serif;text-align:center;padding:60px 14px'>"
                    + "Open WebUI indisponible<br><small>Le serveur démarre ou est arrêté (bouton IA de l'app Pilote). Nouvel essai automatique...</small>",
                    "text/html", "utf-8", null);
                ui.postDelayed(new Runnable() { public void run() { if (alive) web.loadUrl(URL); } }, 4000);
            }
        });
        setContentView(web);
        web.loadUrl(URL);
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req == 1 && chooser != null) {
            chooser.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(res, data));
            chooser = null;
        }
    }

    @Override public void onBackPressed() { if (web.canGoBack()) web.goBack(); else super.onBackPressed(); }
    @Override protected void onResume() { super.onResume(); alive = true; web.onResume(); }
    @Override protected void onPause() { super.onPause(); alive = false; web.onPause(); }
    @Override protected void onDestroy() { super.onDestroy(); alive = false; web.destroy(); }
}
