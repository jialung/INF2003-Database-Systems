-- Smart City Transport Tracker: relational schema (MySQL 8.4)
-- Run through etl/init_mysql.py, or paste into the mysql client.

CREATE DATABASE IF NOT EXISTS transport CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE transport;

DROP TABLE IF EXISTS audit_log, favourite_stop, user_account, station_line, train_station,
    train_line, route_stop, bus_service, bus_stop, operator;

CREATE TABLE operator (
    operator_code VARCHAR(10) PRIMARY KEY,
    operator_name VARCHAR(60) NOT NULL
);

CREATE TABLE bus_stop (
    bus_stop_code CHAR(5)      PRIMARY KEY,
    road_name     VARCHAR(60)  NOT NULL,
    description   VARCHAR(80)  NOT NULL,
    latitude      DECIMAL(9,6) NOT NULL,
    longitude     DECIMAL(9,6) NOT NULL,
    -- Derived from lat/lon so the two can never disagree; spatial index for "stops near me".
    location POINT GENERATED ALWAYS AS (
        ST_GeomFromText(CONCAT('POINT(', longitude, ' ', latitude, ')'), 4326, 'axis-order=long-lat')
    ) STORED SRID 4326 NOT NULL,
    SPATIAL INDEX idx_bus_stop_location (location),
    INDEX idx_bus_stop_road (road_name),
    CONSTRAINT chk_bus_stop_code CHECK (bus_stop_code REGEXP '^[0-9]{5}$'),
    -- Non-standard constraint: every stop must lie in the Singapore / Johor Bahru service area.
    CONSTRAINT chk_bus_stop_area CHECK (latitude BETWEEN 1.15 AND 1.60 AND longitude BETWEEN 103.55 AND 104.15)
);

-- LTA publishes one row per service per direction, and frequencies differ by direction.
-- Frequencies arrive as ranges such as "08-12" and are stored as integer minutes.
CREATE TABLE bus_service (
    service_no       VARCHAR(5)  NOT NULL,
    direction        TINYINT     NOT NULL,
    operator_code    VARCHAR(10) NOT NULL,
    category         VARCHAR(20) NOT NULL,
    origin_code      CHAR(5),
    destination_code CHAR(5),
    am_peak_min      SMALLINT, am_peak_max    SMALLINT,
    am_offpeak_min   SMALLINT, am_offpeak_max SMALLINT,
    pm_peak_min      SMALLINT, pm_peak_max    SMALLINT,
    pm_offpeak_min   SMALLINT, pm_offpeak_max SMALLINT,
    loop_desc        VARCHAR(60),
    PRIMARY KEY (service_no, direction),
    CONSTRAINT fk_service_operator FOREIGN KEY (operator_code) REFERENCES operator (operator_code),
    CONSTRAINT fk_service_origin FOREIGN KEY (origin_code) REFERENCES bus_stop (bus_stop_code),
    CONSTRAINT fk_service_destination FOREIGN KEY (destination_code) REFERENCES bus_stop (bus_stop_code),
    CONSTRAINT chk_service_direction CHECK (direction IN (1, 2)),
    CONSTRAINT chk_am_peak CHECK (am_peak_min > 0 AND am_peak_min <= am_peak_max),
    CONSTRAINT chk_am_offpeak CHECK (am_offpeak_min > 0 AND am_offpeak_min <= am_offpeak_max),
    CONSTRAINT chk_pm_peak CHECK (pm_peak_min > 0 AND pm_peak_min <= pm_peak_max),
    CONSTRAINT chk_pm_offpeak CHECK (pm_offpeak_min > 0 AND pm_offpeak_min <= pm_offpeak_max)
);

-- Resolves the service-stop many-to-many relationship; one row per stop on a route.
CREATE TABLE route_stop (
    service_no    VARCHAR(5)   NOT NULL,
    direction     TINYINT      NOT NULL,
    stop_sequence SMALLINT     NOT NULL,
    bus_stop_code CHAR(5)      NOT NULL,
    distance_km   DECIMAL(5,1) NOT NULL,
    wd_first_bus  TIME, wd_last_bus  TIME,
    sat_first_bus TIME, sat_last_bus TIME,
    sun_first_bus TIME, sun_last_bus TIME,
    PRIMARY KEY (service_no, direction, stop_sequence),
    -- Covers "which services stop here" and the self-join journey queries.
    INDEX idx_route_stop_stop (bus_stop_code, service_no, direction),
    CONSTRAINT fk_route_service FOREIGN KEY (service_no, direction)
        REFERENCES bus_service (service_no, direction) ON DELETE CASCADE,
    CONSTRAINT fk_route_stop FOREIGN KEY (bus_stop_code) REFERENCES bus_stop (bus_stop_code),
    CONSTRAINT chk_route_sequence CHECK (stop_sequence > 0),
    CONSTRAINT chk_route_distance CHECK (distance_km >= 0)
);

CREATE TABLE train_line (
    line_code VARCHAR(5)  PRIMARY KEY,
    line_name VARCHAR(60) NOT NULL,
    line_type VARCHAR(5)  NOT NULL,
    CONSTRAINT chk_line_type CHECK (line_type IN ('MRT', 'LRT'))
);

CREATE TABLE train_station (
    station_id   INT AUTO_INCREMENT PRIMARY KEY,
    station_name VARCHAR(60)  NOT NULL UNIQUE,
    latitude     DECIMAL(9,6),
    longitude    DECIMAL(9,6)
);

-- Resolves the line-station many-to-many relationship; the station code (e.g. NS24) is per line.
CREATE TABLE station_line (
    station_code VARCHAR(5) PRIMARY KEY,
    station_id   INT        NOT NULL,
    line_code    VARCHAR(5) NOT NULL,
    UNIQUE KEY uq_station_line (station_id, line_code),
    CONSTRAINT fk_station_line_station FOREIGN KEY (station_id) REFERENCES train_station (station_id) ON DELETE CASCADE,
    CONSTRAINT fk_station_line_line FOREIGN KEY (line_code) REFERENCES train_line (line_code)
);

CREATE TABLE user_account (
    user_id       INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(30)  NOT NULL UNIQUE,
    email         VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(10)  NOT NULL DEFAULT 'commuter',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_user_role CHECK (role IN ('commuter', 'admin')),
    CONSTRAINT chk_user_email CHECK (email LIKE '%_@_%._%')
);

-- Resolves the user-stop many-to-many relationship.
CREATE TABLE favourite_stop (
    user_id       INT         NOT NULL,
    bus_stop_code CHAR(5)     NOT NULL,
    label         VARCHAR(40),
    added_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, bus_stop_code),
    CONSTRAINT fk_fav_user FOREIGN KEY (user_id) REFERENCES user_account (user_id) ON DELETE CASCADE,
    CONSTRAINT fk_fav_stop FOREIGN KEY (bus_stop_code) REFERENCES bus_stop (bus_stop_code) ON DELETE CASCADE
);

-- Written only by triggers (03_triggers.sql); the app account can read it but not change it.
CREATE TABLE audit_log (
    log_id     BIGINT AUTO_INCREMENT PRIMARY KEY,
    table_name VARCHAR(30) NOT NULL,
    action     VARCHAR(6)  NOT NULL,
    row_key    VARCHAR(40) NOT NULL,
    old_data   JSON,
    new_data   JSON,
    changed_by VARCHAR(100) NOT NULL,
    changed_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_audit_time (changed_at),
    CONSTRAINT chk_audit_action CHECK (action IN ('INSERT', 'UPDATE', 'DELETE'))
);

INSERT INTO operator (operator_code, operator_name) VALUES
    ('SBST', 'SBS Transit'),
    ('SMRT', 'SMRT Buses'),
    ('TTS',  'Tower Transit Singapore'),
    ('GAS',  'Go-Ahead Singapore');
