package com.rab3ounstat.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.view.KeyEvent;
import android.webkit.JsResult;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;

/**
 * L'interface web du PC en plein ecran.
 *
 * Tout le calcul (modele, cotes, journal) reste dans `python web.py --reseau`
 * sur le PC, ou dans la version en ligne que publier.py met a jour chaque
 * matin sur GitHub Pages : l'application ne fait qu'afficher la page. Elle
 * ouvre la version en ligne par defaut ; le lien « changer » en bas de page
 * (adresse rab3ounstat://adresse) ou la fenetre « serveur injoignable »
 * permettent de passer au PC.
 */
public class MainActivity extends Activity {

    private static final String PREFS = "foot_edge";
    private static final String CLE_ADRESSE = "adresse";
    private static final String ADRESSE_PAR_DEFAUT = "192.168.1.108:8000";
    private static final String EN_LIGNE = "https://saifrbai.github.io/betstat/";

    private WebView vue;
    private AlertDialog fenetre;

    @Override
    protected void onCreate(Bundle etat) {
        super.onCreate(etat);
        vue = new WebView(this);
        vue.setBackgroundColor(0xFF16171A);
        WebSettings reglages = vue.getSettings();
        reglages.setJavaScriptEnabled(true);
        reglages.setDomStorageEnabled(true);
        // Jamais de page perimee : une cote affichee doit etre celle du serveur.
        reglages.setCacheMode(WebSettings.LOAD_NO_CACHE);
        // La page s'en sert pour afficher le lien « changer » de source.
        reglages.setUserAgentString(reglages.getUserAgentString() + " RAB3OUNSTAT-App");
        vue.setWebViewClient(new Client());
        vue.setWebChromeClient(new Fenetres());
        setContentView(vue);

        if (etat != null) {
            vue.restoreState(etat);
        } else {
            if (adresse() == null) {
                prefs().edit().putString(CLE_ADRESSE, EN_LIGNE).apply();
            }
            charger();
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle etat) {
        super.onSaveInstanceState(etat);
        vue.saveState(etat);
    }

    @Override
    public boolean onKeyDown(int code, KeyEvent evenement) {
        if (code == KeyEvent.KEYCODE_BACK && vue.canGoBack()) {
            vue.goBack();
            return true;
        }
        return super.onKeyDown(code, evenement);
    }

    @Override
    protected void onDestroy() {
        fermerFenetre();
        vue.destroy();
        super.onDestroy();
    }

    // -- adresse du serveur ----------------------------------------------------

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    private String adresse() {
        return prefs().getString(CLE_ADRESSE, null);
    }

    /** « 192.168.1.108:8000 » devient « http://192.168.1.108:8000/ ». */
    private static String normaliser(String saisie) {
        String a = saisie.trim();
        if (a.isEmpty()) {
            return null;
        }
        if (!a.startsWith("http://") && !a.startsWith("https://")) {
            a = "http://" + a;
        }
        if (!a.endsWith("/")) {
            a = a + "/";
        }
        return Uri.parse(a).getHost() == null ? null : a;
    }

    private void charger() {
        vue.loadUrl(adresse());
    }

    private void demanderAdresse() {
        fermerFenetre();
        final EditText champ = new EditText(this);
        champ.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        String actuelle = adresse();
        champ.setText(actuelle != null && !actuelle.equals(EN_LIGNE)
                ? actuelle : ADRESSE_PAR_DEFAUT);
        champ.setSelectAllOnFocus(true);
        fenetre = new AlertDialog.Builder(this)
                .setTitle("Source des pronos")
                .setMessage("Version en ligne : partout, recalculee chaque matin, "
                        + "lecture seule.\n\nMon PC : l'adresse qu'affiche "
                        + "`python web.py --reseau` (meme Wi-Fi), tout est modifiable.")
                .setView(champ)
                .setCancelable(false)
                .setNeutralButton("Version en ligne", (d, i) -> {
                    prefs().edit().putString(CLE_ADRESSE, EN_LIGNE).apply();
                    charger();
                })
                .setPositiveButton("Mon PC", (d, i) -> {
                    String a = normaliser(champ.getText().toString());
                    if (a == null) {
                        demanderAdresse();
                        return;
                    }
                    prefs().edit().putString(CLE_ADRESSE, a).apply();
                    charger();
                })
                .show();
    }

    private void signalerInjoignable(String detail) {
        fermerFenetre();
        fenetre = new AlertDialog.Builder(this)
                .setTitle("Serveur injoignable")
                .setMessage("Impossible de joindre " + adresse() + "\n\n" + detail
                        + (EN_LIGNE.equals(adresse())
                            ? "\n\nVerifie la connexion Internet du telephone."
                            : "\n\nVerifie que le PC est allume, que `python web.py --reseau` "
                              + "tourne, et que le telephone est sur le meme Wi-Fi. "
                              + "Hors de chez toi : « Changer de source », puis "
                              + "« Version en ligne »."))
                .setCancelable(false)
                .setPositiveButton("Reessayer", (d, i) -> charger())
                .setNeutralButton("Changer de source", (d, i) -> demanderAdresse())
                .setNegativeButton("Quitter", (d, i) -> finish())
                .show();
    }

    private void fermerFenetre() {
        if (fenetre != null && fenetre.isShowing()) {
            fenetre.dismiss();
        }
        fenetre = null;
    }

    private class Client extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest requete) {
            // Le serveur reste dans l'application ; un lien sortant (site d'un
            // bookmaker) part dans le navigateur du telephone.
            Uri cible = requete.getUrl();
            if ("rab3ounstat".equals(cible.getScheme())) {
                demanderAdresse();
                return true;
            }
            String serveur = Uri.parse(adresse()).getAuthority();
            if (serveur != null && serveur.equals(cible.getAuthority())) {
                return false;
            }
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, cible));
            } catch (ActivityNotFoundException aucun) {
                // aucun navigateur : le lien est simplement ignore
            }
            return true;
        }

        @Override
        public void onReceivedError(WebView v, WebResourceRequest requete,
                                    WebResourceError erreur) {
            // Seule la page elle-meme compte : un appel /api rate est deja
            // affiche par la page, avec son propre message.
            if (requete.isForMainFrame()) {
                v.loadData("<html><body style='background:#16171a'></body></html>",
                        "text/html", "utf-8");
                signalerInjoignable(String.valueOf(erreur.getDescription()));
            }
        }

        @Override
        public void onReceivedHttpError(WebView v, WebResourceRequest requete,
                                        WebResourceResponse reponse) {
            // Un 404 de GitHub Pages (version pas encore publiee) laisserait
            // sinon une page sans aucun moyen de changer de source.
            if (requete.isForMainFrame() && reponse.getStatusCode() >= 400) {
                signalerInjoignable("Erreur HTTP " + reponse.getStatusCode());
            }
        }
    }

    /**
     * Sans ce client, une WebView ignore alert() et repond « non » a tout
     * confirm() : le rafraichissement des cotes, qui demande confirmation avant
     * de depenser des credits API, ne partirait jamais.
     */
    private class Fenetres extends WebChromeClient {
        @Override
        public boolean onJsAlert(WebView v, String url, String message, JsResult resultat) {
            new AlertDialog.Builder(MainActivity.this)
                    .setMessage(message)
                    .setCancelable(false)
                    .setPositiveButton("OK", (d, i) -> resultat.confirm())
                    .show();
            return true;
        }

        @Override
        public boolean onJsConfirm(WebView v, String url, String message, JsResult resultat) {
            new AlertDialog.Builder(MainActivity.this)
                    .setMessage(message)
                    .setCancelable(false)
                    .setPositiveButton("OK", (d, i) -> resultat.confirm())
                    .setNegativeButton("Annuler", (d, i) -> resultat.cancel())
                    .show();
            return true;
        }
    }
}
