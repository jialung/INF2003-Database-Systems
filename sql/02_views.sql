USE transport;

-- Every service that calls at each stop.
CREATE OR REPLACE VIEW v_stop_services AS
SELECT s.bus_stop_code, s.description, s.road_name,
       r.service_no, r.direction, r.stop_sequence, r.distance_km,
       bs.operator_code, bs.category, r.wd_first_bus, r.wd_last_bus
FROM bus_stop s
JOIN route_stop r   ON r.bus_stop_code = s.bus_stop_code
JOIN bus_service bs ON bs.service_no = r.service_no AND bs.direction = r.direction;

-- One row per service direction with its operator, terminals, length and stop count.
CREATE OR REPLACE VIEW v_service_summary AS
SELECT bs.service_no, bs.direction, o.operator_name, bs.category,
       bs.origin_code, org.description AS origin_desc,
       bs.destination_code, dst.description AS destination_desc,
       COUNT(r.stop_sequence) AS stop_count,
       MAX(r.distance_km)     AS route_km,
       bs.am_peak_min, bs.am_peak_max, bs.pm_peak_min, bs.pm_peak_max
FROM bus_service bs
JOIN operator o       ON o.operator_code = bs.operator_code
LEFT JOIN bus_stop org ON org.bus_stop_code = bs.origin_code
LEFT JOIN bus_stop dst ON dst.bus_stop_code = bs.destination_code
LEFT JOIN route_stop r ON r.service_no = bs.service_no AND r.direction = bs.direction
GROUP BY bs.service_no, bs.direction, o.operator_name, bs.category, bs.origin_code, org.description,
         bs.destination_code, dst.description, bs.am_peak_min, bs.am_peak_max, bs.pm_peak_min, bs.pm_peak_max;

-- Number of distinct services at each stop (a service counted once even if it loops past twice).
CREATE OR REPLACE VIEW v_stop_service_count AS
SELECT s.bus_stop_code, s.description, s.road_name, s.latitude, s.longitude,
       COUNT(DISTINCT r.service_no) AS service_count
FROM bus_stop s
LEFT JOIN route_stop r ON r.bus_stop_code = s.bus_stop_code
GROUP BY s.bus_stop_code, s.description, s.road_name, s.latitude, s.longitude;

-- Stations served by more than one line.
CREATE OR REPLACE VIEW v_interchange_station AS
SELECT ts.station_id, ts.station_name,
       COUNT(DISTINCT sl.line_code) AS line_count,
       GROUP_CONCAT(sl.station_code ORDER BY sl.station_code SEPARATOR ' / ') AS station_codes
FROM train_station ts
JOIN station_line sl ON sl.station_id = ts.station_id
GROUP BY ts.station_id, ts.station_name
HAVING COUNT(DISTINCT sl.line_code) > 1;
