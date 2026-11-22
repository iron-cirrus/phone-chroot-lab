package fr.pilote;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.res.ColorStateList;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.Switch;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/** Synthese du telephone (page /resume du tableau de bord) + pilotage root en demi-cartes. */
public class MainActivity extends Activity {
    static final String URL = "http://127.0.0.1:8090/resume";
    static final String CTL = "/data/adb/phone-ctl.sh";

    static final int BG = 0xFF0E1116, CARD = 0xFF171C24, LINE = 0xFF262D38, TXT = 0xFFE6EDF3, MUT = 0xFF8B96A5;
    static final int GREEN = 0xFF56D364, RED = 0xFFFF7B72, GREY = 0xFF4B5563, BLUE = 0xFF58A6FF;
    // fonds sombres teintes par type de carte (memes teintes que la page de synthese)
    static final int T_PROC = 0xFF15233A, T_SVC = 0xFF16291C, T_AI = 0xFF2C1A30;
    static final int B_PROC = 0xFF27416A, B_SVC = 0xFF2A4D33, B_AI = 0xFF52305A;   // bordures de la meme teinte

    static final int[] POL = {0, 3, 7};
    static final String[] GOV = {"auto", "powersave", "uag", "performance"};
    static final String[] GOV_LABEL = {"Auto", "Éco", "Normal", "Max"};

    final Handler ui = new Handler(Looper.getMainLooper());
    final ExecutorService actions = Executors.newSingleThreadExecutor();
    final ExecutorService pollers = Executors.newSingleThreadExecutor();
    final AtomicBoolean busy = new AtomicBoolean(false);
    final AtomicBoolean polling = new AtomicBoolean(false);
    final List<View> controls = new ArrayList<>();
    final Map<String, Switch> switches = new HashMap<>();
    final Map<String, TextView> states = new HashMap<>();
    final Map<String, View> dots = new HashMap<>();
    SeekBar govBar;
    TextView govState, govClamp;
    boolean touching = false;
    boolean updating = false;   // vrai pendant une mise a jour par programme (ne pas declencher d'action)
    boolean alive = true;
    float density;
    WebView web;
    TextView msg;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        density = getResources().getDisplayMetrics().density;
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);

        ScrollView sv = new ScrollView(this);
        sv.setBackgroundColor(BG);
        sv.setFillViewport(true);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(5), 0, dp(5), dp(12));
        sv.addView(root, new ViewGroup.LayoutParams(-1, -2));

        buildWeb(root);

        // messages de l'app : barre fixe en bas de l'ecran, deux lignes au plus
        msg = new TextView(this);
        msg.setTextColor(MUT);
        msg.setTextSize(12);
        msg.setMaxLines(2);
        msg.setEllipsize(android.text.TextUtils.TruncateAt.END);
        msg.setBackgroundColor(CARD);
        msg.setPadding(dp(14), dp(8), dp(14), dp(10));
        msg.setText("Prêt.");

        // --- services et gouverneur : demi-cartes, deux par ligne ---
        // le gouverneur se regle dans la page (bloc CPU, a cote du Cortex-X3) via le pont Android
        // le chroot se commande depuis le titre de la page, le gouverneur depuis le bloc CPU (pont Android)

        LinearLayout screen = new LinearLayout(this);
        screen.setOrientation(LinearLayout.VERTICAL);
        screen.setBackgroundColor(BG);
        screen.addView(sv, new LinearLayout.LayoutParams(-1, 0, 1));
        screen.addView(msg, new LinearLayout.LayoutParams(-1, -2));
        setContentView(screen);
        web.loadUrl(URL);
    }

    // ------------------------------------------------------------------ page de synthese
    void buildWeb(LinearLayout root) {
        web = new WebView(this);
        WebSettings ws = web.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setCacheMode(WebSettings.LOAD_NO_CACHE);
        web.setBackgroundColor(BG);
        web.setVerticalScrollBarEnabled(false);
        web.setHorizontalScrollBarEnabled(false);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        web.addJavascriptInterface(new Object() {
            @JavascriptInterface
            public void gov(final String g) {
                if (!g.matches("auto|powersave|uag|performance")) return;
                ui.post(() -> act("gov all " + g, "Gouverneur des 3 blocs : " + g));
            }

            @JavascriptInterface
            public void boost() { ui.post(() -> act("perf-boost", "Réarmement de la performance")); }

            @JavascriptInterface
            public void open(final String what) {
                if (what.equals("moonlight")) ui.post(() -> act("open-moonlight", "Ouverture de Moonlight"));
                if (what.equals("webui")) ui.post(() -> act("open-webui", "Ouverture d'Open WebUI"));
            }

            @JavascriptInterface
            public void ai(final String n, final String a) {
                if (!n.matches("llm|olala") || !a.matches("start|stop")) return;
                String nom = n.equals("llm") ? "Qwen (NPU)" : "Olala (CPU)";
                String confirm = n.equals("olala") && a.equals("start")
                    ? "D\u00e9marrer Olala ?\nLe serveur NPU (Qwen) est arr\u00eat\u00e9 pour lib\u00e9rer la m\u00e9moire." : null;
                String doing = (a.equals("start") ? "D\u00e9marrage : " : "Arr\u00eat : ") + nom;
                ui.post(() -> confirmThen(confirm, () -> act(n + "-" + a, doing), null));
            }

            @JavascriptInterface
            public void svc(final String n, final String a) {
                if (!n.matches("vnc|sunshine|virgl") || !a.matches("start|stop")) return;
                String nom = n.equals("vnc") ? "VNC" : n.equals("sunshine") ? "Sunshine" : "VirGL";
                String confirm = n.equals("vnc") && a.equals("stop")
                    ? "Arrêter VNC ?\nLa session de bureau et ses fenêtres ouvertes sont perdues (Sunshine s'arrête aussi)." : null;
                String doing = (a.equals("start") ? "Démarrage : " : "Arrêt : ") + nom;
                ui.post(() -> confirmThen(confirm, () -> act(n + "-" + a, doing), null));
            }

            @JavascriptInterface
            public void chroot(final String a) {
                if (!a.matches("start|stop|reboot")) return;
                String confirm = a.equals("stop") ? "Arr\u00eater le chroot ?\nLe bureau, SSH, WireGuard et ce tableau de bord s'arr\u00eatent."
                    : a.equals("reboot") ? "Red\u00e9marrer le chroot ?\nLe bureau et les connexions SSH sont coup\u00e9s pendant ~1 min." : null;
                String doing = a.equals("start") ? "D\u00e9marrage du chroot" : a.equals("stop") ? "Arr\u00eat du chroot" : "Red\u00e9marrage du chroot";
                ui.post(() -> confirmThen(confirm, () -> act("chroot-" + a, doing), null));
            }

            @JavascriptInterface
            public void h(final int cssPx) {
                ui.post(() -> {
                    ViewGroup.LayoutParams lp = web.getLayoutParams();
                    int want = (int) (cssPx * density);
                    if (lp.height != want) { lp.height = want; web.setLayoutParams(lp); }
                });
            }
        }, "Android");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView v, String url) {
                if (!url.startsWith("http")) return;
                v.evaluateJavascript(
                    "(function(){var s=document.createElement('style');"
                    + "s.textContent='.nav{display:none}.hd{padding-top:0!important;padding-bottom:0!important;min-height:24px!important;margin-bottom:2px!important}body{padding:0!important;background:transparent!important}.wrap{max-width:none!important}"
                    + ".card{padding:0!important;border:0!important;background:transparent!important;border-radius:0!important}';"
                    + "document.head.appendChild(s);var w=document.querySelector('.wrap');"
                    + "function r(){Android.h(Math.ceil(w.getBoundingClientRect().height)+4)}"
                    + "new ResizeObserver(r).observe(w);r();})();", null);
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest req, WebResourceError err) {
                if (!req.isForMainFrame()) return;
                v.loadDataWithBaseURL(null,
                    "<body style='background:#0e1116;color:#8b96a5;font:14px sans-serif;text-align:center;padding:40px 10px'>"
                    + "Tableau de bord indisponible<br><button onclick=\"Android.chroot('start')\" style=\"margin:16px 0;padding:10px 18px;border:0;border-radius:10px;background:#56d364;color:#0e1116;font-weight:700\">Démarrer le chroot</button><br><small>chroot arrêté ou en démarrage ? Nouvel essai automatique...</small>",
                    "text/html", "utf-8", null);
                ui.postDelayed(() -> { if (alive) web.loadUrl(URL); }, 4000);
            }
        });
        LinearLayout.LayoutParams wlp = new LinearLayout.LayoutParams(-1, dp(380));
        wlp.leftMargin = 0;
        wlp.rightMargin = 0;
        root.addView(web, wlp);
    }

    // ------------------------------------------------------------------ construction de l'interface
    int dp(int v) { return (int) (v * density + .5f); }

    GradientDrawable box(int fill, int stroke, int radiusDp) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(fill);
        g.setCornerRadius(dp(radiusDp));
        if (stroke != 0) g.setStroke(dp(1), stroke);
        return g;
    }

    TextView text(String s, int color, float size, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextColor(color);
        t.setTextSize(size);
        if (bold) t.setTypeface(t.getTypeface(), Typeface.BOLD);
        return t;
    }

    /** Une ligne de deux demi-cartes. */
    LinearLayout row(LinearLayout root) {
        LinearLayout r = new LinearLayout(this);
        r.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(6);
        root.addView(r, lp);
        return r;
    }

    /** Une demi-carte : titre a gauche, actions facultatives a droite, contenu dessous. */
    LinearLayout halfCard(LinearLayout row, String title, View... headerViews) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(box(CARD, LINE, 10));
        card.setPadding(dp(10), dp(8), dp(8), dp(8));
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(0, -1, 1);
        if (row.getChildCount() > 0) clp.leftMargin = dp(6);
        row.addView(card, clp);
        LinearLayout head = new LinearLayout(this);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView t = text(title, TXT, 13, true);
        t.setSingleLine(true);
        t.setEllipsize(android.text.TextUtils.TruncateAt.END);
        head.addView(t, new LinearLayout.LayoutParams(0, -2, 1));
        for (View v : headerViews) {
            LinearLayout.LayoutParams vlp = v.getLayoutParams() instanceof LinearLayout.LayoutParams
                ? (LinearLayout.LayoutParams) v.getLayoutParams() : new LinearLayout.LayoutParams(-2, -2);
            vlp.leftMargin = dp(4);
            head.addView(v, vlp);
        }
        card.addView(head);
        return card;
    }

    /** Petit bouton rond avec un symbole (eclair, fleche de redemarrage). */
    Button iconButton(String symbol, String action, String doing, String confirm) {
        Button bt = new Button(this);
        bt.setText(symbol);
        bt.setTextSize(15);
        bt.setTextColor(TXT);
        bt.setAllCaps(false);
        bt.setPadding(0, 0, 0, 0);
        bt.setMinWidth(0);
        bt.setMinimumWidth(0);
        bt.setMinHeight(0);
        bt.setMinimumHeight(0);
        bt.setBackground(box(LINE, 0, 15));
        bt.setLayoutParams(new LinearLayout.LayoutParams(dp(30), dp(30)));
        bt.setOnClickListener(v -> confirmThen(confirm, () -> act(action, doing), null));
        controls.add(bt);
        return bt;
    }

    /** Demi-carte de service : etat (pastille + texte), icone de redemarrage et interrupteur facultatifs. */
    void serviceCard(LinearLayout row, String key, String title, String start, String stop, String restart,
                     String confirmOn, String confirmOff, String confirmRestart) {
        LinearLayout card = halfCard(row, title);
        card.setBackground(box(T_SVC, B_SVC, 10));
        LinearLayout body = new LinearLayout(this);
        body.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(-1, -2);
        blp.topMargin = dp(4);
        card.addView(body, blp);
        View dot = new View(this);
        dot.setBackground(box(GREY, 0, 4));
        body.addView(dot, new LinearLayout.LayoutParams(dp(8), dp(8)));
        dots.put(key, dot);
        TextView st = text("...", MUT, 11, false);
        LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(0, -2, 1);
        slp.leftMargin = dp(6);
        body.addView(st, slp);
        states.put(key, st);
        if (restart != null) {
            Button r = iconButton("↻", restart, "Redémarrage : " + title, confirmRestart);
            LinearLayout.LayoutParams rlp = (LinearLayout.LayoutParams) r.getLayoutParams();
            rlp.rightMargin = dp(4);
            body.addView(r);
        }
        if (start != null) {
            final Switch sw = new Switch(this);
            styleSwitch(sw);
            sw.setOnCheckedChangeListener((btn, on) -> {
                if (updating) return;
                String action = on ? start : stop;
                String doing = (on ? "Démarrage : " : "Arrêt : ") + title;
                confirmThen(on ? confirmOn : confirmOff, () -> act(action, doing), () -> setQuiet(sw, !on));
            });
            body.addView(sw);
            switches.put(key, sw);
            controls.add(sw);
        }
    }

    /** Carte "IA locale" pleine largeur : Qwen (NPU) et Olala (CPU) cote a cote, un interrupteur chacun. */
    void aiCard(LinearLayout row) {
        LinearLayout card = halfCard(row, "IA locale");
        ((LinearLayout) card.getChildAt(0)).addView(new BrainIcon(this), new LinearLayout.LayoutParams(dp(17), dp(17)));   // cerveau monochrome, coin haut droit
        card.setBackground(box(T_AI, B_AI, 10));
        LinearLayout body = new LinearLayout(this);
        body.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(-1, -2);
        blp.topMargin = dp(4);
        card.addView(body, blp);
        aiEntry(body, "llm", "Qwen (NPU)", "llm-start", "llm-stop", null);
        aiEntry(body, "olala", "Olala (CPU)", "olala-start", "olala-stop",
            "Démarrer Olala ?\nLe serveur NPU (Qwen) est arrêté pour libérer la mémoire.");
    }

    void aiEntry(LinearLayout body, String key, String name, String start, String stop, String confirmOn) {
        LinearLayout e = new LinearLayout(this);
        e.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams elp = new LinearLayout.LayoutParams(0, -2, 1);
        if (body.getChildCount() > 0) elp.leftMargin = dp(10);
        body.addView(e, elp);
        View dot = new View(this);
        dot.setBackground(box(GREY, 0, 4));
        e.addView(dot, new LinearLayout.LayoutParams(dp(8), dp(8)));
        dots.put(key, dot);
        LinearLayout names = new LinearLayout(this);
        names.setOrientation(LinearLayout.VERTICAL);
        TextView label = text(name, TXT, 12, false);
        label.setPaintFlags(label.getPaintFlags() | android.graphics.Paint.UNDERLINE_TEXT_FLAG);
        label.setOnClickListener(v -> act("open-webui", "Ouverture d'Open WebUI"));
        names.addView(label);
        TextView st = text("...", MUT, 10, false);
        names.addView(st);
        states.put(key, st);
        LinearLayout.LayoutParams nlp = new LinearLayout.LayoutParams(0, -2, 1);
        nlp.leftMargin = dp(6);
        e.addView(names, nlp);
        final Switch sw = new Switch(this);
        styleSwitch(sw);
        sw.setOnCheckedChangeListener((btn, on) -> {
            if (updating) return;
            String doing = (on ? "Démarrage : " : "Arrêt : ") + name;
            confirmThen(on ? confirmOn : null, () -> act(on ? start : stop, doing), () -> setQuiet(sw, !on));
        });
        e.addView(sw);
        switches.put(key, sw);
        controls.add(sw);
    }

    /** Demi-carte du gouverneur : cadenas si plafond inattendu, eclair, curseur a 4 positions (3 blocs CPU). */
    void govCard(LinearLayout row) {
        govClamp = text("🔒", TXT, 13, false);   // cadenas : frequence max plafonnee (constructeur ou thermique)
        govClamp.setVisibility(View.GONE);
        Button boost = iconButton("⚡", "perf-boost", "Réarmement de la performance", null);
        LinearLayout card = halfCard(row, "Gouverneur", govClamp, boost);
        card.setBackground(box(T_PROC, B_PROC, 10));
        final SeekBar sb = new SeekBar(this);
        sb.setMax(3);
        sb.setPadding(dp(8), 0, dp(8), 0);
        sb.setThumbTintList(ColorStateList.valueOf(BLUE));
        sb.setProgressTintList(ColorStateList.valueOf(BLUE));
        sb.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            public void onProgressChanged(SeekBar s, int p, boolean fromUser) { }
            public void onStartTrackingTouch(SeekBar s) { touching = true; }
            public void onStopTrackingTouch(SeekBar s) {
                touching = false;
                act("gov all " + GOV[s.getProgress()], "Gouverneur des 3 blocs : " + GOV_LABEL[s.getProgress()]);
            }
        });
        card.addView(sb, new LinearLayout.LayoutParams(-1, dp(28)));
        LinearLayout lg = new LinearLayout(this);
        for (int k = 0; k < 4; k++) {
            TextView t = text(GOV_LABEL[k], MUT, 9, false);
            t.setGravity(k == 0 ? Gravity.START : k == 3 ? Gravity.END : Gravity.CENTER);
            lg.addView(t, new LinearLayout.LayoutParams(0, -2, 1));
        }
        card.addView(lg);
        govState = text("...", MUT, 10, false);
        card.addView(govState);
        govBar = sb;
        controls.add(sb);
    }

    void confirmThen(String confirm, Runnable ok, Runnable cancel) {
        if (confirm == null) { ok.run(); return; }
        new AlertDialog.Builder(this)
            .setMessage(confirm)
            .setNegativeButton("Annuler", (d, w) -> { if (cancel != null) cancel.run(); })
            .setOnCancelListener(d -> { if (cancel != null) cancel.run(); })
            .setPositiveButton("Continuer", (d, w) -> ok.run())
            .show();
    }

    void setQuiet(Switch sw, boolean on) { updating = true; sw.setChecked(on); updating = false; }

    // ------------------------------------------------------------------ actions et etat
    @Override protected void onResume() { super.onResume(); alive = true; schedulePoll(0); web.onResume(); }
    @Override protected void onPause() { super.onPause(); alive = false; web.onPause(); }
    @Override protected void onDestroy() { super.onDestroy(); alive = false; actions.shutdownNow(); pollers.shutdownNow(); web.destroy(); }

    void setBusy(boolean b) {
        for (View x : controls) { x.setEnabled(!b); x.setAlpha(b ? .45f : 1f); }
    }

    void act(final String action, String doing) {
        if (!busy.compareAndSet(false, true)) return;
        setBusy(true);
        msg.setTextColor(MUT);
        msg.setText("En cours : " + doing + "...");
        actions.submit(() -> {
            final String out = su(actionShell, CTL + " " + action);
            final String last = lastLine(out);
            ui.post(() -> {
                busy.set(false);
                setBusy(false);
                boolean bad = last.startsWith("ECHEC") || last.isEmpty() || last.contains("denied");
                msg.setTextColor(bad ? RED : GREEN);
                msg.setText(last.isEmpty() ? "Aucune réponse : root refusé ? (autoriser l'app dans Magisk)" : last);
                if (action.startsWith("chroot") || action.startsWith("vnc")) web.loadUrl(URL);
            });
            schedulePoll(500);
        });
    }

    final Runnable pollTask = () -> {
        if (!alive) return;
        if (polling.compareAndSet(false, true)) {
            pollers.submit(() -> {
                final String out = su(statusShell, CTL + " status");
                ui.post(() -> { polling.set(false); showStatus(out); });
            });
        }
        schedulePoll(4000);
    };

    void schedulePoll(long delay) {
        ui.removeCallbacks(pollTask);
        ui.postDelayed(pollTask, delay);
    }

    static String lastLine(String s) {
        String r = "";
        for (String l : s.split("\n")) if (!l.trim().isEmpty()) r = l.trim();
        return r;
    }

    void showStatus(String out) {
        Map<String, String> m = new HashMap<>();
        for (String l : out.split("\n")) { int i = l.indexOf('='); if (i > 0) m.put(l.substring(0, i).trim(), l.substring(i + 1).trim()); }
        if (!m.containsKey("chroot")) {
            for (TextView t : states.values()) t.setText("?");
            if (!busy.get()) { msg.setTextColor(RED); msg.setText("Root indisponible : autorisez l'app dans Magisk (Superuser)."); }
            return;
        }
        updating = true;
        for (String k : new String[]{"llm", "olala"}) {
            boolean on = "1".equals(m.get(k));
            if (dots.get(k) == null) continue;
            dots.get(k).setBackground(box(on ? GREEN : RED, 0, 4));
            states.get(k).setText(on ? "actif" : "arrêté");
            Switch sw = switches.get(k);
            if (sw != null && !busy.get() && sw.isChecked() != on) sw.setChecked(on);
        }
        // gouverneur commun aux 3 blocs : "mixte" si les blocs different (reglage fait ailleurs ou garde-fou thermique)
        String g0 = m.get("gov0"), g3 = m.get("gov3"), g7 = m.get("gov7");
        boolean same = g0 != null && g0.equals(g3) && g0.equals(g7);
        int nManual = 0;
        for (int p : POL) if ("1".equals(m.get("man" + p))) nManual++;
        int pos = 0;
        if (nManual > 0) {
            pos = 2;
            for (int k = 1; k < 4; k++) if (GOV[k].equals(g3)) pos = k;
        }
        updating = false;
    }

    static int parse(String s) { try { return Integer.parseInt(s); } catch (Exception e) { return 0; } }

    /** Shell root persistant : un seul `su` (donc une seule notification Magisk) par shell. */
    static class RootShell {
        Process p; java.io.OutputStream in; BufferedReader out;
        synchronized String run(String cmd) {
            for (int attempt = 0; attempt < 2; attempt++) {
                try {
                    if (p == null) {
                        p = new ProcessBuilder("su", "-mm").redirectErrorStream(true).start();
                        in = p.getOutputStream();
                        out = new BufferedReader(new InputStreamReader(p.getInputStream(), "UTF-8"));
                    }
                    String mark = "__FIN__" + System.nanoTime();
                    in.write((cmd + "\necho " + mark + "\n").getBytes("UTF-8"));
                    in.flush();
                    StringBuilder sb = new StringBuilder();
                    String l;
                    while ((l = out.readLine()) != null) {
                        if (l.equals(mark)) return sb.toString();
                        sb.append(l).append('\n');
                    }
                    p = null; // le shell s'est ferme (root refuse ?)
                } catch (Exception e) {
                    p = null;
                }
            }
            return "";
        }
    }

    static final RootShell statusShell = new RootShell(), actionShell = new RootShell();

    /** Interrupteur a piste ronde et pastille centree, vert quand actif (meme dessin que la page). */
    void styleSwitch(Switch sw) {
        int w = dp(30), h = dp(16), k = dp(12);
        GradientDrawable on = new GradientDrawable(); on.setColor(GREEN); on.setCornerRadius(h / 2f); on.setSize(w, h);
        GradientDrawable off = new GradientDrawable(); off.setColor(0xFF4B5563); off.setCornerRadius(h / 2f); off.setSize(w, h);
        android.graphics.drawable.StateListDrawable track = new android.graphics.drawable.StateListDrawable();
        track.addState(new int[]{android.R.attr.state_checked}, on);
        track.addState(new int[]{}, off);
        // la pastille est un cercle blanc place dans un cadre de la hauteur de la piste : centrage vertical exact
        GradientDrawable dot = new GradientDrawable(); dot.setShape(GradientDrawable.OVAL); dot.setColor(0xFFFFFFFF); dot.setSize(k, k);
        android.graphics.drawable.InsetDrawable thumb = new android.graphics.drawable.InsetDrawable(dot, dp(2), (h - k) / 2, dp(2), (h - k) / 2);
        sw.setTrackDrawable(track);
        sw.setThumbDrawable(thumb);
        sw.setSwitchMinWidth(w);
    }


    /** Icone "cerveau" monochrome (deux hemispheres + replis), trait de la couleur du texte comme les autres icones. */
    static class BrainIcon extends View {
        final android.graphics.Paint p = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
        final android.graphics.Path half = new android.graphics.Path();
        final android.graphics.Path folds = new android.graphics.Path();
        BrainIcon(android.content.Context c) {
            super(c);
            p.setColor(TXT);
            p.setStyle(android.graphics.Paint.Style.STROKE);
            p.setStrokeCap(android.graphics.Paint.Cap.ROUND);
            p.setStrokeJoin(android.graphics.Paint.Join.ROUND);
            // hemisphere gauche sur une grille 24 x 24 ; le droit est son image miroir autour de x = 12
            half.moveTo(12, 4.5f);
            half.cubicTo(10.6f, 2.8f, 7.6f, 3.2f, 7.1f, 5.6f);
            half.cubicTo(4.7f, 5.8f, 3.5f, 8.6f, 5.0f, 10.4f);
            half.cubicTo(3.3f, 12.2f, 3.9f, 15.4f, 6.2f, 16.0f);
            half.cubicTo(6.6f, 18.6f, 10.0f, 19.7f, 12.0f, 18.2f);
            half.lineTo(12, 4.5f);
            folds.moveTo(8.2f, 9.6f);
            folds.cubicTo(9.2f, 10.6f, 10.6f, 10.6f, 11.2f, 9.4f);
            folds.moveTo(7.6f, 13.4f);
            folds.cubicTo(8.8f, 12.6f, 10.4f, 12.8f, 11.2f, 14.0f);
        }
        @Override protected void onDraw(android.graphics.Canvas c) {
            float s = getWidth() / 24f;
            p.setStrokeWidth(1.7f);                      // en unites de la grille 24 x 24 (le canvas est mis a l'echelle ci-dessous)
            c.save(); c.scale(s, s);
            c.drawPath(half, p); c.drawPath(folds, p);
            c.scale(-1, 1, 12, 0);                      // hemisphere droit
            c.drawPath(half, p); c.drawPath(folds, p);
            c.restore();
        }
    }

    static String su(RootShell s, String cmd) { return s.run(cmd); }
}
