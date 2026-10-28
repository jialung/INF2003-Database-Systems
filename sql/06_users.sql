-- Least-privilege accounts. etl/init_mysql.py fills in the {{...}} placeholders from .env.
USE transport;

DROP USER IF EXISTS '{{RW_USER}}'@'%', '{{RO_USER}}'@'%';
CREATE USER '{{RW_USER}}'@'%' IDENTIFIED BY '{{RW_PASSWORD}}';
CREATE USER '{{RO_USER}}'@'%' IDENTIFIED BY '{{RO_PASSWORD}}';

-- app_rw: what the web app needs for CRUD. It can read the audit log but never alter it;
-- only the triggers (running as their definer) write there.
GRANT SELECT, INSERT, UPDATE, DELETE ON transport.bus_stop       TO '{{RW_USER}}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON transport.bus_service    TO '{{RW_USER}}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON transport.route_stop     TO '{{RW_USER}}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON transport.user_account   TO '{{RW_USER}}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON transport.favourite_stop TO '{{RW_USER}}'@'%';
GRANT SELECT ON transport.operator      TO '{{RW_USER}}'@'%';
GRANT SELECT ON transport.train_line    TO '{{RW_USER}}'@'%';
GRANT SELECT ON transport.train_station TO '{{RW_USER}}'@'%';
GRANT SELECT ON transport.station_line  TO '{{RW_USER}}'@'%';
GRANT SELECT ON transport.audit_log     TO '{{RW_USER}}'@'%';
GRANT SELECT ON transport.v_stop_services       TO '{{RW_USER}}'@'%';
GRANT SELECT ON transport.v_service_summary     TO '{{RW_USER}}'@'%';
GRANT SELECT ON transport.v_stop_service_count  TO '{{RW_USER}}'@'%';
GRANT SELECT ON transport.v_interchange_station TO '{{RW_USER}}'@'%';
GRANT EXECUTE ON PROCEDURE transport.sp_insert_route_stop TO '{{RW_USER}}'@'%';
GRANT EXECUTE ON PROCEDURE transport.sp_delete_route_stop TO '{{RW_USER}}'@'%';

-- app_ro: analytics and benchmarks. Read-only, and column-level grants keep it away
-- from password hashes and e-mail addresses.
GRANT SELECT ON transport.operator       TO '{{RO_USER}}'@'%';
GRANT SELECT ON transport.bus_stop       TO '{{RO_USER}}'@'%';
GRANT SELECT ON transport.bus_service    TO '{{RO_USER}}'@'%';
GRANT SELECT ON transport.route_stop     TO '{{RO_USER}}'@'%';
GRANT SELECT ON transport.train_line     TO '{{RO_USER}}'@'%';
GRANT SELECT ON transport.train_station  TO '{{RO_USER}}'@'%';
GRANT SELECT ON transport.station_line   TO '{{RO_USER}}'@'%';
GRANT SELECT ON transport.favourite_stop TO '{{RO_USER}}'@'%';
GRANT SELECT (user_id, username, role, created_at) ON transport.user_account TO '{{RO_USER}}'@'%';
GRANT SELECT ON transport.v_stop_services       TO '{{RO_USER}}'@'%';
GRANT SELECT ON transport.v_service_summary     TO '{{RO_USER}}'@'%';
GRANT SELECT ON transport.v_stop_service_count  TO '{{RO_USER}}'@'%';
GRANT SELECT ON transport.v_interchange_station TO '{{RO_USER}}'@'%';
GRANT SELECT ON transport.od_volume             TO '{{RO_USER}}'@'%';
GRANT SELECT ON transport.pv_bus_stop           TO '{{RO_USER}}'@'%';
