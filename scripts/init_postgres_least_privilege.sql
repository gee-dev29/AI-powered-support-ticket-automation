-- Least-Privilege PostgreSQL Role Setup for Support System
-- Run this as superuser (postgres) during initial database provisioning.

-- 1. Create Migration / DDL Admin User
CREATE USER support_admin WITH PASSWORD 'REPLACE_WITH_STRONG_ADMIN_PASSWORD';
GRANT CONNECT ON DATABASE support TO support_admin;
GRANT ALL PRIVILEGES ON SCHEMA public TO support_admin;

-- 2. Create Application Runtime User (FastAPI)
-- Needs DML (SELECT, INSERT, UPDATE) on application tables, but NO DDL (DROP, ALTER, CREATE)
CREATE USER support_app WITH PASSWORD 'REPLACE_WITH_STRONG_APP_PASSWORD';
GRANT CONNECT ON DATABASE support TO support_app;
GRANT USAGE ON SCHEMA public TO support_app;

GRANT SELECT, INSERT, UPDATE ON TABLE 
    customers, 
    departments, 
    routing_rules, 
    support_requests, 
    notifications, 
    audit_events 
TO support_app;

-- 3. Create Automation Worker User (n8n Notification Processor)
-- Needs access ONLY to outbox notifications and reading support request metadata
CREATE USER support_n8n WITH PASSWORD 'REPLACE_WITH_STRONG_N8N_PASSWORD';
GRANT CONNECT ON DATABASE support TO support_n8n;
GRANT USAGE ON SCHEMA public TO support_n8n;

GRANT SELECT, UPDATE ON TABLE notifications TO support_n8n;
GRANT SELECT ON TABLE support_requests TO support_n8n;
GRANT SELECT ON TABLE customers TO support_n8n;

-- Prevent public access
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
