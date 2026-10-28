---
name: evidence-driven-validation
description: Choisir les vérifications ciblées et ne pas confondre tests, CI et certification.
---

Lire `AI_ENTRYPOINT.md` et `PHASE_CERTIFICATION.md` ; ce skill ne change aucun gate.

1. Identifier contrat modifié, consommateurs, risque, tests existants et OS requis.
2. Lancer seulement les tests ciblés pendant la PR Draft ; consigner SHA, commande, environnement, résultat, éléments non exécutés.
3. Auditer les commits individuellement, puis l'intégration. Ne jamais convertir une revue IA en verdict de test.
4. Sur PR finalisée, exiger `Holding Certification / Required` sur le HEAD exact avant de parler de validation CI ; garder l'acceptation Windows réelle distincte.
5. En cas de test absent, ignoré ou échec, conclure `NOT_VERIFIED` ou `BLOCKED`, pas PASS.
