# Annonce Discord — Mise à jour du bot (2026-07-16)

À poster avec `/announce`.

- **Titre** (champ du modal) : `Les paris, toute l'année`
- **Message** (champ du modal) : tout ce qui suit la ligne de séparation.

La syntaxe est celle de Discord, pas celle de GitHub — `-#` produit du petit texte gris, et
les blocs de code servent à aligner les commandes. Ça ne rendra donc pas pareil ici que sur
Discord, c'est normal.

1200 caractères, largement sous la limite de 2000. Plus court que celle du 11-07 (1677) :
c'est une mise à jour, pas une présentation du bot.

Les deux listes sont **vérifiées**, chacune contre la source qui fait foi :

- **LoL** — `GET /lol/leagues` : LEC (4197), LFL (4292), LCK (293), Worlds (297),
  Mid-Season Invitational (300), Esports World Cup (5262). L'« Esports Nations Cup » n'existe
  pas chez PandaScore, et Worlds s'appelle `Worlds`, pas « World Championship » — les deux
  ont été corrigés dans le code.
- **Foot** — les 12 compétitions du plan gratuit football-data : `WC`, `EC`, `CL` et `FL1` en
  font partie, donc pas de 403. Les grands championnats (PL, Liga, Serie A, Bundesliga) sont
  disponibles mais volontairement écartés : un week-end, c'est ~48 cartes dans un seul salon.

⚠️ Reste un point à confirmer : que `FOOTBALL_DATA_API_KEY` soit bien définie côté Komodo.
Au boot, le log doit dire `Betting providers loaded: football_data, pandascore` — si
`football_data` manque, aucune carte foot n'apparaîtra et il faut retirer la ligne **Foot**
de l'annonce.

---

## Les paris, toute l'année

Avant : la Coupe du Monde, et pas grand-chose entre deux.

**Foot** — Ligue des Champions · Ligue 1 · Coupe du Monde · Euro
**LoL** — LEC · LFL · LCK · Worlds · MSI · EWC

-# Les cartes apparaissent toutes seules, une semaine avant le match.

## Chaque compétition a son salon

Le **foot**, la **LEC/LFL**, la **LCK**, l'**international** et les **paris perso** ont chacun le leur. Une soirée de Ligue des Champions n'enterre plus les cartes LoL.

-# Chaque salon a un **message épinglé** : ce qui est suivi là, et les prochains matchs. Un salon sans carte = pas de match prévu, pas un bug.

## Fini les fausses alertes

> :warning:  Le bot annonçait **« le résultat n'est jamais arrivé »**… pendant que le match était encore en cours.

Un match de foot dure ~2h, et le bot s'inquiétait au bout de 2h pile. Il attend maintenant que le match soit **vraiment** fini.
-# Un seul faux rappel par match, c'était déjà assez pour ne plus croire les vrais.

## Rien ne change pour toi

Une carte apparaît → **tu cliques, tu mises.** Les cotes bougent toujours, et tu es payé à la **cote finale**, pas à celle affichée quand tu mises.

-# `/help` reste la seule commande à retenir.
