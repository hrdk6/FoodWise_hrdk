-- Runs once, as the postgres superuser, when the data volume is first created.
-- The application connects as `foodwise`, a non-superuser that owns its databases.
-- Superusers bypass row-level security, so the app must never use the postgres role.
CREATE ROLE foodwise LOGIN PASSWORD 'foodwise';
CREATE DATABASE foodwise OWNER foodwise;
CREATE DATABASE foodwise_test OWNER foodwise;

\connect foodwise
CREATE EXTENSION IF NOT EXISTS cube;
CREATE EXTENSION IF NOT EXISTS earthdistance;
GRANT ALL ON SCHEMA public TO foodwise;

\connect foodwise_test
CREATE EXTENSION IF NOT EXISTS cube;
CREATE EXTENSION IF NOT EXISTS earthdistance;
GRANT ALL ON SCHEMA public TO foodwise;
