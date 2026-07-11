# Annonce Discord — Mise à jour du bot (2026-07-11)

À poster avec `/announce`.

- **Titre** (champ du modal) : `Mise à jour du bot`
- **Message** (champ du modal) : tout ce qui suit la ligne de séparation.

La syntaxe est celle de Discord, pas celle de GitHub — `-#` produit du petit texte gris, et
les blocs de code servent à aligner les commandes. Ça ne rendra donc pas pareil ici que sur
Discord, c'est normal.

~1450 caractères, largement sous la limite de 2000.

---

## Pour commencer

```
/help    tout ce que le bot sait faire
```
-# La seule commande à retenir. Elle n'affiche que ce qui est réellement activé sur le serveur.

## FloshCoins

```
/claim     +100 par jour  (reset à minuit)
/balance   ton solde
```
-# Tu as reçu **1000** :coin: de départ. Ça sert à parier.

## Les paris

Une carte apparaît dans le salon des paris → **tu cliques sur ton option, tu mises.** C'est tout.

> :warning:  **Les cotes bougent. Comprends ça avant de miser.**
> Les gagnants **se partagent les mises des perdants** :
> ​
> - Plus il y a de monde sur ton option → **moins** tu gagnes
> - Tout le monde parie pareil → cote **1.00x**, tu ne gagnes **rien**
> - Personne sur le gagnant → **toutes les mises sont perdues**
> - Tu es payé à la **cote finale**, pas à celle affichée quand tu mises

**Une seule option par pari.** Tu peux renforcer ton camp, jamais parier sur les deux.

```
/bet create    lance ton propre pari   (coûte 100)
/bet mine      tes paris en cours
/bet resolve   clôture ton pari
```
-# Tant que tu n'as pas fait `/bet resolve`, **les mises restent bloquées**. Le bot te relancera si tu oublies. Les 100 :coin: te sont rendus si tu annules alors que d'autres avaient misé.

## Files d'attente

Panneau → un jeu → une taille. Les autres rejoignent avec le bouton **Rejoindre**.
-# **Seul l'hôte** peut désormais fermer sa file — avant, n'importe qui pouvait.

## Anniversaires

```
/birthday set <jour> <mois> <année>
```
-# Tu es souhaité automatiquement le jour J.

## Musique et corrections

- Les **paroles** marchent enfin
- Les boutons du lecteur survivent aux redémarrages
- Une commande qui échoue t'explique **pourquoi**
