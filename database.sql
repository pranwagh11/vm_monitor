-- =========================================================
-- DATABASE
-- =========================================================

CREATE DATABASE IF NOT EXISTS agent_monitoring;
USE agent_monitoring;

-- =========================================================
-- AGENTS
-- =========================================================
-- Aid is primary key
-- agent_id is NOT unique
-- sys_info updated after system_info event

CREATE TABLE IF NOT EXISTS agents (
    Aid BIGINT NOT NULL AUTO_INCREMENT,
    agent_id VARCHAR(100) NOT NULL,
    status VARCHAR(20) DEFAULT 'offline',
    last_seen DOUBLE,
    sys_info TEXT NULL,

    PRIMARY KEY (Aid),

    INDEX idx_agent_id (agent_id),
    INDEX idx_status (status),
    INDEX idx_last_seen (last_seen)
);

-- =========================================================
-- ALERT EVENTS
-- =========================================================

CREATE TABLE IF NOT EXISTS agent_events (
    id BIGINT NOT NULL AUTO_INCREMENT,

    agent_id VARCHAR(100) NOT NULL,
    timestamp DOUBLE NOT NULL,

    event_type VARCHAR(100) NOT NULL,

    cpu FLOAT,
    ram FLOAT,
    disk FLOAT,

    PRIMARY KEY (id),

    INDEX idx_agent_id (agent_id),
    INDEX idx_timestamp (timestamp),
    INDEX idx_event_type (event_type)
);

-- =========================================================
-- PROCESS HISTORY
-- =========================================================

CREATE TABLE IF NOT EXISTS process_history (
    id BIGINT NOT NULL AUTO_INCREMENT,

    agent_id VARCHAR(100) NOT NULL,
    timestamp DOUBLE NOT NULL,

    data LONGTEXT,

    PRIMARY KEY (id),

    INDEX idx_agent_id (agent_id),
    INDEX idx_timestamp (timestamp)
);

-- =========================================================
-- LOG HISTORY
-- =========================================================

CREATE TABLE IF NOT EXISTS log_history (
    id BIGINT NOT NULL AUTO_INCREMENT,

    agent_id VARCHAR(100) NOT NULL,
    timestamp DOUBLE NOT NULL,

    data LONGTEXT,

    PRIMARY KEY (id),

    INDEX idx_agent_id (agent_id),
    INDEX idx_timestamp (timestamp)
);

-- =========================================================
-- METRICS HISTORY
-- =========================================================

CREATE TABLE IF NOT EXISTS metrics_history (
    id BIGINT NOT NULL AUTO_INCREMENT,

    agent_id VARCHAR(100) NOT NULL,
    timestamp DOUBLE NOT NULL,

    cpu FLOAT,
    ram FLOAT,
    disk FLOAT,
    net FLOAT,

    PRIMARY KEY (id),

    INDEX idx_agent_id (agent_id),
    INDEX idx_timestamp (timestamp),
    INDEX idx_agent_timestamp (agent_id, timestamp)
);

-- =========================================================
-- OPTIONAL CLEANUP QUERIES
-- =========================================================

-- Delete all data
-- TRUNCATE TABLE agents;
-- TRUNCATE TABLE agent_events;
-- TRUNCATE TABLE process_history;
-- TRUNCATE TABLE log_history;
-- TRUNCATE TABLE metrics_history;

-- =========================================================
-- END
-- =========================================================
