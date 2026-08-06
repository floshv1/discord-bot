-- Rapatrier les données d'une ancienne guilde vers la nouvelle.
--
-- À lancer À LA MAIN, une seule fois, quand le bot change de serveur Discord sur une base
-- existante. Ce fichier n'est PAS dans bot/db/migrations/ et ne doit jamais y aller : les
-- migrations sont concaténées et rejouées à chaque démarrage (bot/core/bot.py), donc un
-- UPDATE avec deux IDs de guilde figés dedans se rejouerait éternellement.
--
-- Pourquoi c'est nécessaire : currency_wallets et birthdays sont clés sur user_id SEUL,
-- guild_id n'est qu'un tampon. Les lignes des membres qui existaient déjà n'ont donc pas été
-- recréées par les /setup, et pointent encore sur l'ancienne guilde — invisibles pour toutes
-- les lectures qui filtrent dessus (leaderboard, historique, embeds anniversaires).
--
-- Usage :
--   docker compose exec db pg_dump -U botuser discord_bot > backup-avant-move.sql
--   docker compose exec -T db psql -U botuser -d discord_bot \
--       -v old=<ANCIEN_GUILD_ID> -v new=<NOUVEAU_GUILD_ID> < scripts/move_guild.sql
--
-- Pour voir d'abord ce qu'il y a en base (lecture seule) :
--   SELECT 'currency_wallets' t, guild_id, count(*) FROM currency_wallets GROUP BY 2
--   UNION ALL SELECT 'birthdays', guild_id, count(*) FROM birthdays GROUP BY 2
--   UNION ALL SELECT 'voice_sessions', guild_id, count(*) FROM voice_sessions GROUP BY 2
--   UNION ALL SELECT 'betting_markets', guild_id, count(*) FROM betting_markets GROUP BY 2
--   ORDER BY 1, 2;

\set ON_ERROR_STOP on

BEGIN;

-- Coins : soldes ET ledger, ensemble. Les séparer casserait la réconciliation
-- SUM(currency_transactions.amount) == currency_wallets.balance côté nouvelle guilde.
UPDATE currency_wallets      SET guild_id = :new WHERE guild_id = :old;
UPDATE currency_transactions SET guild_id = :new WHERE guild_id = :old;

UPDATE birthdays      SET guild_id = :new WHERE guild_id = :old;
UPDATE voice_sessions SET guild_id = :new WHERE guild_id = :old;

-- Paris. betting_bets n'a pas de guild_id (il pend de market_id) — rien à y faire.
-- Les cartes des marchés encore ouverts pointent sur des salons de l'ancienne guilde :
-- _ensure_card (betting/cog.py) ne les retrouve pas, tombe dans le `send`, et les re-poste
-- dans le bon salon en conservant les mises. Les marchés dont l'échéance est passée seront
-- lockés puis auto-voidés par _void_stuck, ce qui rembourse via adjust() — ledger intact.
UPDATE betting_markets SET guild_id = :new WHERE guild_id = :old;

-- Presets de queue, historique modération et tribunal.
UPDATE game_presets       SET guild_id = :new WHERE guild_id = :old;
UPDATE game_subscriptions SET guild_id = :new WHERE guild_id = :old;
UPDATE mod_actions        SET guild_id = :new WHERE guild_id = :old;
UPDATE reprimands         SET guild_id = :new WHERE guild_id = :old;
UPDATE tribunal_trials    SET guild_id = :new WHERE guild_id = :old;

-- Pur historique — décommenter si tu veux le garder consultable.
-- UPDATE suggestions     SET guild_id = :new WHERE guild_id = :old;
-- UPDATE audit_logs      SET guild_id = :new WHERE guild_id = :old;
-- UPDATE music_history   SET guild_id = :new WHERE guild_id = :old;
-- UPDATE music_commands  SET guild_id = :new WHERE guild_id = :old;

-- CRITIQUE. start_log_cursor_at_latest a posé le curseur à MAX(id) des transactions de la
-- NOUVELLE guilde, donc très bas ; les lignes qu'on vient de rapatrier ont toutes un id
-- supérieur. Sans ce recalage, drain_new_transactions déverse TOUT l'historique de coins
-- dans le salon de logs au prochain tick.
UPDATE currency_log_cursor
   SET last_transaction_id = (SELECT COALESCE(MAX(id), 0) FROM currency_transactions)
 WHERE guild_id = :new;

-- Contrôle avant COMMIT : le ledger doit toujours réconcilier. 0 ligne attendue.
SELECT w.user_id, w.balance, COALESCE(SUM(t.amount), 0) AS ledger
  FROM currency_wallets w
  LEFT JOIN currency_transactions t ON t.user_id = w.user_id
 GROUP BY w.user_id, w.balance
HAVING w.balance <> COALESCE(SUM(t.amount), 0);

COMMIT;

-- NE PAS toucher aux tables de config : elles ont été réécrites par les /setup et contiennent
-- des channel_id / message_id du NOUVEAU serveur. Les migrer ferait pointer le bot sur des
-- messages supprimés, et guild_id y est clé primaire (conflit garanti) :
--   currency_leaderboard, birthday_config, voice_leaderboard, betting_config,
--   betting_channels, music_config, palworld_config, queue_config, suggestion_config,
--   reprimand_config, announce_config, birthday_announcements, music_state,
--   game_queues / queue_members (files en cours, transitoires).
