# Journal des versions

## 2.0.0 · Wiki Inspi Windows + iPhone app (novembre 2026)

Version Windows et iPhone de [wiki-inspi](https://github.com/aion88/wiki-inspi) (V1, macOS).

### Ajouté
- **Windows** : `install.bat`, icône sur le bureau, icône dans le menu Démarrer et dans la barre des tâches, lancement au démarrage, une seule instance à la fois, Gestionnaire d'identifiants, script de pare‑feu, commande `inspi.bat`.
- **App iPhone** : appairage par QR code, Raccourci dans la feuille de partage (images et liens), double tape au dos de l'iPhone pour envoyer une capture, app web installable, conversion des photos HEIC.
- **Liens** : récupération de l'image d'une page (og:image), de l'original HD pour Pinterest, du titre, du site et de l'auteur ; adresses privées refusées.
- **IA au choix** : Ollama en local ou Claude (API Anthropic), repli automatique, test d'une fiche, contrôle de complétude avec nouvelle demande automatique, filtre des fiches incomplètes.
- **Fiche riche** : palette nommée avec la part de chaque couleur, couleur principale, température, mouvement, technique, époque, sujet, composition, lumière, typo, texture, références, usages (liste fermée), thèmes, auteur estimé avec niveau de confiance.
- **Galerie** : curseur de taille, recherche par couleur (Lab), filtres, 6 tris, images similaires, familles, doublons, corbeille avec Annuler, glisser‑déposer, Ctrl+V, diagnostic, interface responsive.
- **Éditeur d'images** : rogner (9 formats) et redimensionner, remplacer en gardant l'original ou enregistrer une copie, transparence PNG conservée.
- **Moodboards** : création depuis une sélection ou une recherche, onglet avec aperçus, ajout sans rien déplacer même dans un board compressé, sauvegarde avant chaque écriture, titre, légendes et palette.
- **Extension Chrome et Edge** : appairage automatique, image, texte, lien ou page entière, lecture de l'auteur, envoi du lien si l'image ne se télécharge pas, notifications.
- **Obsidian** : note d'accueil, vues Bases, notes de thème, installation automatique des plugins, import des notes Web Clipper.
- **Doublons** : une image identique n'est pas recréée, sa nouvelle source est ajoutée à la fiche existante.

### Sécurité
- Jeton d'appairage, vérification de l'origine et de l'hôte (protection contre le DNS rebinding), journal des refus.
- Texte des pages transmis à l'IA comme donnée (protection contre l'injection de consignes).
- Clés et réglages exclus du dépôt (`.gitignore`).
