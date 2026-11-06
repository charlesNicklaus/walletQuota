-- Run with psql connected to the online server's postgres maintenance database.
\set ON_ERROR_STOP on
SELECT 'CREATE DATABASE sulu_pool'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'sulu_pool')
\gexec

\connect sulu_pool
\ir payout_pool.sql
