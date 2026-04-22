import json
import sqlite3
import uuid
from importlib import import_module
from pathlib import Path
from urllib.parse import urlparse

import streamlit as st


st.set_page_config(
    page_title="Questionnaire Référent - Bilan Carbone",
    layout="centered",
)

if "show_thanks_screen" not in st.session_state:
    st.session_state.show_thanks_screen = False


STORAGE_BUCKET = "factures-referent"


_MIME_MAP = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _get_mime(filename: str, streamlit_type: str) -> str:
    ext = Path(filename).suffix.lower()
    return _MIME_MAP.get(ext) or streamlit_type or "application/octet-stream"


def _upload_file_to_storage(client, uploaded_file, prefix: str) -> str:
    """Upload un UploadedFile vers Supabase Storage et retourne l'URL publique.
    Lève une exception explicite en cas d'échec (ne swallows plus silencieusement)."""
    safe_name = uploaded_file.name.replace(" ", "_")
    storage_path = f"{prefix}/{uuid.uuid4()}_{safe_name}"
    file_bytes = uploaded_file.getvalue()
    mime = _get_mime(uploaded_file.name, uploaded_file.type)

    client.storage.from_(STORAGE_BUCKET).upload(
        path=storage_path,
        file=file_bytes,
        file_options={"content-type": mime},
    )

    result = client.storage.from_(STORAGE_BUCKET).get_public_url(storage_path)
    # Selon la version du SDK, get_public_url renvoie str ou dict
    if isinstance(result, dict):
        public_url = result.get("publicURL") or result.get("publicUrl") or result.get("data", {}).get("publicUrl", "")
    else:
        public_url = str(result)
    return public_url


def save_referent_response(reponses: dict) -> tuple[bool, str]:
    errors = []
    supabase_key_name = None
    if "SUPABASE_SERVICE_ROLE_KEY" in st.secrets:
        supabase_key_name = "SUPABASE_SERVICE_ROLE_KEY"
    elif "SUPABASE_KEY" in st.secrets:
        supabase_key_name = "SUPABASE_KEY"

    supabase_configured = "SUPABASE_URL" in st.secrets and supabase_key_name is not None

    try:
        if supabase_configured:
            supabase = import_module("supabase")
            create_client = getattr(supabase, "create_client")

            raw_url = str(st.secrets["SUPABASE_URL"]).strip().rstrip("/")
            parsed = urlparse(raw_url)
            if parsed.scheme and parsed.netloc:
                supabase_url = f"{parsed.scheme}://{parsed.netloc}"
            else:
                supabase_url = raw_url

            client = create_client(supabase_url, str(st.secrets[supabase_key_name]))
            payload = {
                "ville": reponses.get("ville", reponses.get("office", "")),
                "nom": reponses.get("nom", ""),
                "prenom": reponses.get("prenom", ""),
                "poste": reponses.get("poste", ""),
                "email": reponses.get("email", ""),
                "type_chauffage": reponses.get("type_chauffage", ""),
                "surface_bureaux_m2": reponses.get("surface_bureaux_m2", 0),
                "nb_collaborateurs": reponses.get("nombre_total_collaborateurs", 0),
                "conso_elec": reponses.get("consommation_electricite_kwh", 0),
                "reponses": reponses,
            }

            try:
                client.table("questionnaire_referent_reponses").insert(payload).execute()
                return True, "Questionnaire référent enregistré dans Supabase avec succès."
            except Exception:
                minimal_payload = {"reponses": reponses}
                client.table("questionnaire_referent_reponses").insert(minimal_payload).execute()
                return True, (
                    "Questionnaire référent enregistré dans Supabase "
                    "(mode compatibilité schéma minimal)."
                )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Supabase indisponible pour le moment : {exc}")

    try:
        db_path = Path(__file__).parent / "bilan_carbone_local.db"
        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS questionnaire_referent_reponses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    ville TEXT,
                    nom TEXT NOT NULL,
                    prenom TEXT NOT NULL,
                    poste TEXT,
                    email TEXT NOT NULL,
                    reponses TEXT NOT NULL
                )
                """
            )

            existing_columns = {
                row[1] for row in cur.execute("PRAGMA table_info(questionnaire_referent_reponses)").fetchall()
            }
            if "ville" not in existing_columns:
                cur.execute("ALTER TABLE questionnaire_referent_reponses ADD COLUMN ville TEXT")

            cur.execute(
                """
                INSERT INTO questionnaire_referent_reponses (
                    ville,
                    nom,
                    prenom,
                    poste,
                    email,
                    reponses
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    reponses.get("ville", reponses.get("office", "")),
                    reponses.get("nom", ""),
                    reponses.get("prenom", ""),
                    reponses.get("poste", ""),
                    reponses.get("email", ""),
                    json.dumps(reponses, ensure_ascii=False),
                ),
            )
            conn.commit()

        if errors and supabase_configured:
            return False, (
                "Sauvegarde locale effectuée dans SQLite, mais échec Supabase. "
                + " | ".join(errors)
            )
        if errors:
            return True, "Questionnaire enregistré localement dans SQLite. " + " | ".join(errors)
        return True, "Questionnaire enregistré localement dans SQLite (bilan_carbone_local.db)."
    except Exception as exc:  # noqa: BLE001
        return False, f"Échec de l'enregistrement en base locale SQLite : {exc}"


st.markdown(
    '<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&display=swap" rel="stylesheet">',
    unsafe_allow_html=True,
)

st.markdown(
    """
    <style>
        :root {
            --bg-soft: #f4f7fb;
            --ink: #0f172a;
            --muted: #475569;
            --line: #dbe4f0;
        }

        html, body, [class*="css"] {
            font-family: 'Poppins', sans-serif;
            color: var(--ink);
        }

        .stApp {
            background:
                radial-gradient(1200px 500px at 10% -10%, #dbeafe 0%, transparent 60%),
                radial-gradient(900px 450px at 90% -20%, #e0f2fe 0%, transparent 60%),
                linear-gradient(180deg, #f8fbff 0%, var(--bg-soft) 100%);
        }

        .hero {
            padding: 1rem 1.2rem;
            border: 1px solid var(--line);
            border-left: 6px solid #1d4ed8;
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.86);
            margin: 0.25rem 0 1rem 0;
            box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
        }

        .hero h1 {
            margin: 0;
            font-size: 1.2rem;
            font-weight: 800;
            color: #0b3a75;
        }

        .hero p {
            margin: 0.45rem 0 0;
            color: var(--muted);
            font-size: 0.95rem;
        }

        [data-testid="stExpander"] {
            border: 1px solid #dbe4f0;
            border-left: 5px solid #bfdbfe;
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.92);
            margin-bottom: 1rem;
            box-shadow: 0 10px 28px rgba(15, 23, 42, 0.06);
        }

        [data-testid="stExpander"] > details > summary {
            font-size: 1rem;
            font-weight: 700;
            color: #0b3a75;
            letter-spacing: 0.1px;
        }

        .page-title {
            font-size: 1.5rem;
            font-weight: 700;
            color: #0b3a75;
            letter-spacing: -0.3px;
            margin: 0.6rem 0 0.2rem 0;
            line-height: 1.3;
        }

        .page-title span {
            font-weight: 400;
            color: #1d4ed8;
            font-size: 1rem;
            display: block;
            margin-top: 0.1rem;
            letter-spacing: 0.2px;
        }

        .stButton > button {
            width: 100%;
            border-radius: 12px;
            border: none;
            padding: 0.8rem 1.1rem;
            font-weight: 800;
            background: linear-gradient(135deg, #0f4aa3 0%, #1d4ed8 55%, #2563eb 100%);
            color: white;
            box-shadow: 0 10px 24px rgba(29, 78, 216, 0.35);
        }

        .thanks-screen {
            position: fixed;
            inset: 0;
            width: 100vw;
            height: 100vh;
            z-index: 9999;
            display: flex;
            align-items: center;
            justify-content: center;
            background:
                radial-gradient(1200px 500px at 12% -12%, #dbeafe 0%, transparent 60%),
                radial-gradient(900px 450px at 88% -18%, #dcfce7 0%, transparent 58%),
                linear-gradient(180deg, #f8fbff 0%, #edf4fb 100%);
            animation: fadeIn 280ms ease-out;
        }

        .thanks-card {
            text-align: center;
            background: rgba(255, 255, 255, 0.96);
            border: 1px solid #d3deea;
            border-radius: 14px;
            box-shadow: 0 16px 34px rgba(15, 23, 42, 0.09);
            padding: 2rem 1.8rem;
            max-width: 620px;
            margin: 0 auto;
            width: 100%;
        }

        .thanks-leaf {
            font-size: 2.35rem;
            line-height: 1;
            margin-bottom: 0.5rem;
        }

        .thanks-title {
            margin: 0;
            color: #123b6b;
            font-size: 1.55rem;
            font-weight: 700;
            letter-spacing: -0.15px;
        }

        .thanks-subtitle {
            margin: 0.6rem 0 0;
            color: #475569;
            font-size: 0.98rem;
            font-weight: 500;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
        }
    </style>
    """,
    unsafe_allow_html=True,
)

if st.session_state.show_thanks_screen:
    st.markdown(
        """
        <div class="thanks-screen">
            <div class="thanks-card">
                <div class="thanks-leaf">🌱</div>
                <h1 class="thanks-title">Merci pour votre participation !</h1>
                <p class="thanks-subtitle">Vos informations ont bien été enregistrées pour l'analyse du bilan carbone.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

image_path = Path(__file__).parent / "entete_question.png"
if image_path.exists():
    st.image(str(image_path))

st.markdown(
    """
    <div class="page-title">
      Questionnaire Bilan Carbone — Référent
      <span>Vision transversale des secteurs · 2025</span>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="hero">
      <h1>Votre quotidien compte pour le climat 🌱</h1>
      <p>
        Ce questionnaire est destiné à la personne référente qui relie tous les secteurs de la société.
        Merci de compléter les informations ci-dessous pour construire un bilan carbone fiable et actionnable.
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.expander("Office représenté", expanded=True):
    ville = st.selectbox(
        "Office représenté :*",
        ["", "Bordeaux", "Chatou", "Massy", "Lille", "Nice", "Toulouse"],
        format_func=lambda x: "Sélectionnez un office" if x == "" else x,
    )

with st.expander("Coordonnées du référent", expanded=True):
    nom = st.text_input("Nom :*")
    prenom = st.text_input("Prénom :*")
    poste = st.text_input("Poste :*")
    email = st.text_input("Adresse e-mail :*")

with st.expander("Section 1 : Énergie & Locaux", expanded=True):
    type_chauffage = st.selectbox(
        "Type de chauffage :*",
        ["Gaz", "Pompe à chaleur", "Bois", "Fioul", "Électricité"],
    )

    facture_gaz_2025 = st.file_uploader(
        "Facture d'énergie de 2025 (Importez autant de fichiers que nécessaire) :*",
        accept_multiple_files=True,
        key="ref_facture_gaz_2025",
    )

    consommation_electricite = st.number_input(
        "Consommation annuelle d'Électricité (kWh) :*",
        min_value=0,
        step=1,
    )

    facture_electricite_2025 = st.file_uploader(
        "Facture d'électricité de 2025 (Importez autant de fichiers que nécessaire) :*",
        accept_multiple_files=True,
        key="ref_facture_electricite_2025",
    )

    surface_bureaux = st.number_input(
        "Surface totale des bureaux (m²) :*",
        min_value=0,
        step=1,
    )

    plan_locaux = st.file_uploader(
        "Déposer le plan des locaux :*",
        accept_multiple_files=False,
        key="ref_plan_locaux",
    )

    etages_bureaux = st.multiselect(
        "À quel(s) étage(s) se situent vos bureaux ?*",
        ["RDC", "1", "2", "3", "4", "5"],
    )

    payeur_facture_gaz = st.radio(
        "Qui paie les factures d'énergie ?*",
        ["La société d'exploitation", "Le propriétaire via les charges"],
    )

    plusieurs_compteurs = st.text_input(
        "L'étude possède-t-elle plusieurs compteurs d'électricité ? (Si Oui, combien ?) :*"
    )

    quote_part_parties_communes = st.radio(
        "Vos consommations incluent-elles une quote-part des parties communes (ex: ascenseur, hall) ?*",
        ["OUI", "NON"],
    )

    fournisseur_electricite = st.text_input(
        "Quel est le nom de votre fournisseur d'électricité actuel ?*"
    )

    locaux_climatises = st.radio("Les locaux sont-ils climatisés ?", ["OUI", "NON"])

    recharge_gaz_frigorigene = None
    type_gaz_refrigerant = ""
    if locaux_climatises == "OUI":
        recharge_gaz_frigorigene = st.radio(
            "Si oui, y a-t-il eu une recharge de gaz (fluide frigorigène) cette année ?",
            ["OUI", "NON"],
        )
        type_gaz_refrigerant = st.text_input(
            "Type de gaz réfrigérant (si connu) : (Ex : R410A, R32...) :*"
        )

    nombre_total_collaborateurs = st.number_input(
        "Nombre total de collaborateurs :",
        min_value=0,
        step=1,
    )

with st.expander("Section 3 : Déplacements Professionnels", expanded=True):
    kilometrage_annuel_moyen = st.number_input(
        "Kilométrage annuel total effectué avec des véhicules pro/perso pour le travail (en moyenne par collaborateur) :",
        min_value=0,
        step=1,
    )

    nombre_trajets_train = st.number_input(
        "Nombre de trajets en train (si applicable, pour l'ensemble des collaborateurs) :",
        min_value=0,
        step=1,
    )

with st.expander("Section 5 : Équipements & Achats", expanded=True):
    nombre_ordinateurs_portables = st.number_input(
        "Nombre d'ordinateur portables :",
        min_value=0,
        step=1,
    )

    nombre_ecrans_fixes = st.number_input(
        "Nombre d'écrans fixes :",
        min_value=0,
        step=1,
    )

    nombre_nouveaux_pc = st.number_input(
        "Nombre de nouveaux PC achetés (sur 2025) :",
        min_value=0,
        step=1,
    )

    mobilier_neuf = st.text_input(
        "Mobilier neuf (bureaux, chaises) : (Oui/Non, si oui préciser la quantité)"
    )

with st.expander("Section 6 : Déchets & Numérique", expanded=True):
    tri_selectif = st.radio("Le tri sélectif est-il mis en place ?", ["OUI", "NON"])

    volume_dechets_non_recycles = st.text_input(
        "Volume de déchets non recyclés (poubelle noire) : (Estimation : nombre de sacs ou de bacs par semaine)"
    )

    stockage_donnees = st.radio(
        "Où sont stockées vos données ?",
        ["Serveur physique interne à l'étude", "Cloud externe type Microsoft 365 ou logiciel métier"],
    )

    volume_papier = st.text_input(
        "Volume de papier consommé par an (ou nombre de ramettes de papier environ) :"
    )

    volume_envois_postaux = st.text_input(
        "Volume d'envois postaux annuel (Courriers / Recommandés) : (Estimation en nombre de plis ou budget annuel en euros)"
    )

with st.expander("Section 7 : Services Externes", expanded=True):
    societe_nettoyage = st.text_input(
        "Faites-vous appel à une société de nettoyage externe ? Si oui, précisez le nombre d'heures d'intervention par semaine."
    )

st.markdown("---")
if st.button("🚀 Envoyer le questionnaire"):
    erreurs = []

    if not ville:
        erreurs.append("- Office représenté")

    if not nom.strip():
        erreurs.append("- Nom")
    if not prenom.strip():
        erreurs.append("- Prénom")
    if not poste.strip():
        erreurs.append("- Poste")
    if not email.strip():
        erreurs.append("- Adresse e-mail")
    elif "@" not in email or "." not in email.split("@")[-1]:
        erreurs.append("- Adresse e-mail (format invalide)")

    if len(facture_gaz_2025) == 0:
        erreurs.append("- Facture d'énergie de 2025 (au moins 1 fichier)")
    if len(facture_electricite_2025) == 0:
        erreurs.append("- Facture d'électricité de 2025 (au moins 1 fichier)")
    if plan_locaux is None:
        erreurs.append("- Plan des locaux")
    if consommation_electricite <= 0:
        erreurs.append("- Consommation annuelle d'Électricité (kWh)")
    if surface_bureaux <= 0:
        erreurs.append("- Surface totale des bureaux (m²)")
    if not etages_bureaux:
        erreurs.append("- À quel(s) étage(s) se situent vos bureaux ?")
    if not plusieurs_compteurs.strip():
        erreurs.append("- L'étude possède-t-elle plusieurs compteurs d'électricité ?")
    if not fournisseur_electricite.strip():
        erreurs.append("- Nom du fournisseur d'électricité")
    if locaux_climatises == "OUI" and not type_gaz_refrigerant.strip():
        erreurs.append("- Type de gaz réfrigérant (si locaux climatisés)")

    if erreurs:
        st.error("Veuillez corriger les champs suivants :\n\n" + "\n".join(erreurs))
    else:
        # --- Upload des fichiers vers Supabase Storage ---
        urls_gaz = [f.name for f in facture_gaz_2025]
        urls_elec = [f.name for f in facture_electricite_2025]
        url_plan = plan_locaux.name if plan_locaux else None

        supabase_key_name = None
        if "SUPABASE_SERVICE_ROLE_KEY" in st.secrets:
            supabase_key_name = "SUPABASE_SERVICE_ROLE_KEY"
        elif "SUPABASE_KEY" in st.secrets:
            supabase_key_name = "SUPABASE_KEY"

        if "SUPABASE_URL" in st.secrets and supabase_key_name is not None:
            try:
                _supabase = import_module("supabase")
                _create_client = getattr(_supabase, "create_client")
                raw_url = str(st.secrets["SUPABASE_URL"]).strip().rstrip("/")
                parsed = urlparse(raw_url)
                supabase_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else raw_url
                _client = _create_client(supabase_url, str(st.secrets[supabase_key_name]))

                with st.spinner("Envoi des fichiers en cours..."):
                    urls_gaz = [
                        _upload_file_to_storage(_client, f, "factures_gaz")
                        for f in facture_gaz_2025
                    ]
                    urls_elec = [
                        _upload_file_to_storage(_client, f, "factures_elec")
                        for f in facture_electricite_2025
                    ]
                    if plan_locaux is not None:
                        url_plan = _upload_file_to_storage(_client, plan_locaux, "plans_locaux")
            except Exception as upload_err:
                st.error(f"Erreur lors de l'upload des fichiers : {upload_err}")
                st.stop()

        reponses = {
            "ville": ville,
            "office": ville,
            "nom": nom,
            "prenom": prenom,
            "poste": poste,
            "email": email,
            "type_chauffage": type_chauffage,
            "facture_gaz_2025": urls_gaz,
            "consommation_electricite_kwh": consommation_electricite,
            "facture_electricite_2025": urls_elec,
            "surface_bureaux_m2": surface_bureaux,
            "plan_locaux": url_plan,
            "etages_bureaux": etages_bureaux,
            "payeur_facture_gaz": payeur_facture_gaz,
            "plusieurs_compteurs_electricite": plusieurs_compteurs,
            "quote_part_parties_communes": quote_part_parties_communes,
            "fournisseur_electricite": fournisseur_electricite,
            "locaux_climatises": locaux_climatises,
            "recharge_gaz_frigorigene": recharge_gaz_frigorigene,
            "type_gaz_refrigerant": type_gaz_refrigerant,
            "nombre_total_collaborateurs": nombre_total_collaborateurs,
            "kilometrage_annuel_moyen_par_collaborateur": kilometrage_annuel_moyen,
            "nombre_trajets_train": nombre_trajets_train,
            "nombre_ordinateurs_portables": nombre_ordinateurs_portables,
            "nombre_ecrans_fixes": nombre_ecrans_fixes,
            "nombre_nouveaux_pc_2025": nombre_nouveaux_pc,
            "mobilier_neuf": mobilier_neuf,
            "tri_selectif": tri_selectif,
            "volume_dechets_non_recycles": volume_dechets_non_recycles,
            "stockage_donnees": stockage_donnees,
            "volume_papier": volume_papier,
            "volume_envois_postaux": volume_envois_postaux,
            "societe_nettoyage": societe_nettoyage,
        }

        db_ok, db_message = save_referent_response(reponses)
        if db_ok:
            st.session_state.show_thanks_screen = True
            st.rerun()
        else:
            st.error("Le questionnaire est valide, mais l'enregistrement principal a échoué.")
            st.warning(db_message)
