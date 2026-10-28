-- Passenger-volume tables used only by the SQL-vs-NoSQL performance study (bench/).
-- The same CSVs are loaded into MongoDB by etl/load_volume.py. No secondary indexes here:
-- bench/bench_queries.py adds and drops them to measure their effect.
USE transport;

DROP TABLE IF EXISTS od_volume, pv_bus_stop;

CREATE TABLE od_volume (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    period           CHAR(7)     NOT NULL,
    day_type         VARCHAR(20) NOT NULL,
    hour             TINYINT     NOT NULL,
    origin_code      CHAR(5)     NOT NULL,
    destination_code CHAR(5)     NOT NULL,
    trips            INT         NOT NULL,
    CONSTRAINT chk_od_hour CHECK (hour BETWEEN 0 AND 23),
    CONSTRAINT chk_od_trips CHECK (trips >= 0)
);

CREATE TABLE pv_bus_stop (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    period        CHAR(7)     NOT NULL,
    day_type      VARCHAR(20) NOT NULL,
    hour          TINYINT     NOT NULL,
    bus_stop_code CHAR(5)     NOT NULL,
    tap_in        INT         NOT NULL,
    tap_out       INT         NOT NULL,
    CONSTRAINT chk_pv_hour CHECK (hour BETWEEN 0 AND 23)
);
