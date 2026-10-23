# Mises à jour ultra-rapides — Holding (22/10/2026)

## Périmètre
Distinguer **Git source** (PR #16 déjà dédiée à pull/push fast-forward), **publication des candidats Atlas** (PR #7/#11) et **mise à jour de l'application Windows installée**. Pas de protocole Git maison ni de second moteur de synchronisation.

## Conception
- Endpoint GitHub stable de manifestes/releases versionnés ; découverte légère conditionnelle (ETag / If-None-Match, TTL et délai adaptatif, sans requêtes à chaque rafraîchissement UI).
- Manifeste d'artefacts immuables : version, canal stable/test, SHA-256, taille, version du schéma, dépendances et référence du paquet complet ; signature vérifiable en complément des empreintes.
- Préférer **patch différentiel seulement s'il est disponible, sûr, compatible et plus petit** que le paquet ; sinon fallback complet. Ne jamais supposer que git-delta d'un dépôt convient à un EXE empaqueté.
- Téléchargement streaming avec reprise HTTP Range uniquement après contrôle ETag/longueur/checksum ; aucun écrasement de la version active.
- Staging sur même volume, vérification finale, installation atomique quand possible, lancement de validation/smoke Windows, bascule, rollback automatique sur échec.
- L'EXE actif n'est pas remplacé en place : helper/launcher externe et relance contrôlée. Respect du choix de disque et des données utilisateur.
- Réseau mobile : seuil d'usage, cache local, UI progression **réelle** octets reçus/octets attendus, suspension/reprise.
- Une version non certifiée ne passe jamais en canal stable ; aucun push/merge forcé.

## Tests de sortie
- 304 (inchangé), petit correctif, patch incompatible -> complet, coupure au milieu de Range, serveur ignorant Range, manifest altéré, checksum incorrect et rollback.
- Windows natif : fichiers verrouillés, deux installations, interruption alimentation simulée, disque insuffisant, ancien/dernier schéma.
- Mesures distinctes de temps de vérification et téléchargements en octets ; cible rapide, jamais durée promise sans réseau.
- PR #16 reste la base Git source et n'est pas dupliquée ; PR #9/#13 pour l'installateur.

## Statut
Cahier des charges initial, Draft. Aucun updater fonctionnel revendiqué.
