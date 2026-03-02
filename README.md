# ☕ CAYA KAFFEE — Système de menu & commandes

Menu digital avec QR codes par table, commandes en temps réel, et panel admin complet.

---

## 🗺️ Architecture

| URL | Qui | Quoi |
|-----|-----|------|
| `/table/<N>` | Client | Formulaire d'accueil + menu + panier |
| `/staff` | Serveur | Commandes en temps réel (live) |
| `/admin` | Propriétaire | Gestion complète |
| `/login` | Staff/Admin | Connexion |

---

## 🚀 Lancer en local

```bash
# 1. Installer les dépendances
pip install -r requirements.txt

# 2. Initialiser la base de données
python init_db.py

# 3. Lancer
python app.py
```

Ouvrir : http://127.0.0.1:5000

**Compte admin par défaut :**
- Utilisateur : `admin`
- Mot de passe : `admin123`
⚠️ Changez-le après la première connexion !

---

## ☁️ Déployer sur Render.com

### 1. Mettre le projet sur GitHub
1. Créer un dépôt GitHub (`caya-kaffee`)
2. Uploader tous les fichiers

### 2. Créer le service Render
1. [render.com](https://render.com) → **New → Web Service**
2. Connecter le dépôt GitHub
3. Configurer :
   - **Runtime** : Python 3
   - **Build Command** : `pip install -r requirements.txt && python init_db.py`
   - **Start Command** : `gunicorn app:app --worker-class gthread --threads 4 --timeout 120`

### 3. Variables d'environnement (Render → Environment)
| Variable | Valeur |
|----------|--------|
| `SECRET_KEY` | Une longue chaîne aléatoire (ex: `caya-kaffee-2024-xK9mP...`) |
'd43383839513a5849678bee603982ecf'

### 4. Déployer
Cliquer **Create Web Service** → attendre 2-3 minutes.

Votre URL sera : `https://caya-kaffee.onrender.com`

---

## 📱 Générer et imprimer les QR Codes

1. Aller dans Admin → **Tables & QR Codes**
2. Ajouter chaque table (numéro + libellé optionnel)
3. Cliquer **⬇ Télécharger QR** pour chaque table
4. Imprimer et poser sur les tables

Le QR pointe vers : `https://votre-url.onrender.com/table/N`

---

## 👥 Flux client (ce que voit le client)

1. **Scan QR** → arrivée sur `/table/5`
2. **Nouveau client** → formulaire (prénom, nom, téléphone, genre)
3. **Client connu** (cookie) + commande précédente non notée → formulaire avec évaluation ⭐
4. **Client connu** sans note en attente → directement sur le menu
5. **Menu** → ajouter au panier → note optionnelle → confirmer
6. ✅ Commande envoyée → la cuisine reçoit en temps réel

---

## 🔔 Page Staff (temps réel)

URL : `/staff`

- Chaque nouvelle commande apparaît **instantanément** (SSE — pas de rafraîchissement)
- Son de notification à l'arrivée d'une commande
- Bouton "✓ Marquer comme terminé" → retire la carte
- Accessible depuis n'importe quel appareil (téléphone du serveur)

---

## 🔧 Changer le mot de passe admin

```python
from app import app, db, User
from werkzeug.security import generate_password_hash
with app.app_context():
    user = User.query.filter_by(username='admin').first()
    user.password_hash = generate_password_hash('nouveau-mot-de-passe')
    db.session.commit()
    print("Mot de passe changé !")
```

---

## 📁 Structure

```
caya_kaffee/
├── app.py                    ← Application complète (routes, modèles, SSE)
├── init_db.py                ← Initialisation base de données
├── requirements.txt
├── Procfile                  ← Config Render/Gunicorn
├── templates/
│   ├── customer/
│   │   ├── entry.html        ← Formulaire client + notation
│   │   └── menu.html         ← Menu + panier + commande
│   ├── staff/
│   │   └── orders.html       ← Commandes en temps réel
│   └── admin/
│       ├── login.html
│       ├── dashboard.html
│       ├── tables.html       ← Gestion tables + QR codes
│       ├── categories.html
│       ├── edit_category.html
│       ├── products.html
│       ├── edit_product.html
│       ├── orders.html       ← Historique commandes
│       └── staff.html        ← Gestion comptes staff
└── static/
    ├── css/style.css
    ├── uploads/              ← Images des produits
    └── qrcodes/              ← QR codes générés
```
