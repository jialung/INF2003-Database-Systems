USE transport;

-- Audit triggers. The app sets @app_user to the logged-in username before writing,
-- so the log records the person, not just the shared database account.

DROP TRIGGER IF EXISTS trg_bus_stop_ai;
DROP TRIGGER IF EXISTS trg_bus_stop_au;
DROP TRIGGER IF EXISTS trg_bus_stop_ad;
DROP TRIGGER IF EXISTS trg_bus_service_ai;
DROP TRIGGER IF EXISTS trg_bus_service_au;
DROP TRIGGER IF EXISTS trg_bus_service_ad;
DROP TRIGGER IF EXISTS trg_route_stop_ai;
DROP TRIGGER IF EXISTS trg_route_stop_au;
DROP TRIGGER IF EXISTS trg_route_stop_ad;
DROP TRIGGER IF EXISTS trg_favourite_limit;

DELIMITER $$

CREATE TRIGGER trg_bus_stop_ai AFTER INSERT ON bus_stop FOR EACH ROW
BEGIN
    INSERT INTO audit_log (table_name, action, row_key, new_data, changed_by)
    VALUES ('bus_stop', 'INSERT', NEW.bus_stop_code,
            JSON_OBJECT('road_name', NEW.road_name, 'description', NEW.description,
                        'latitude', NEW.latitude, 'longitude', NEW.longitude),
            COALESCE(@app_user, USER()));
END$$

CREATE TRIGGER trg_bus_stop_au AFTER UPDATE ON bus_stop FOR EACH ROW
BEGIN
    INSERT INTO audit_log (table_name, action, row_key, old_data, new_data, changed_by)
    VALUES ('bus_stop', 'UPDATE', NEW.bus_stop_code,
            JSON_OBJECT('road_name', OLD.road_name, 'description', OLD.description,
                        'latitude', OLD.latitude, 'longitude', OLD.longitude),
            JSON_OBJECT('road_name', NEW.road_name, 'description', NEW.description,
                        'latitude', NEW.latitude, 'longitude', NEW.longitude),
            COALESCE(@app_user, USER()));
END$$

CREATE TRIGGER trg_bus_stop_ad AFTER DELETE ON bus_stop FOR EACH ROW
BEGIN
    INSERT INTO audit_log (table_name, action, row_key, old_data, changed_by)
    VALUES ('bus_stop', 'DELETE', OLD.bus_stop_code,
            JSON_OBJECT('road_name', OLD.road_name, 'description', OLD.description,
                        'latitude', OLD.latitude, 'longitude', OLD.longitude),
            COALESCE(@app_user, USER()));
END$$

CREATE TRIGGER trg_bus_service_ai AFTER INSERT ON bus_service FOR EACH ROW
BEGIN
    INSERT INTO audit_log (table_name, action, row_key, new_data, changed_by)
    VALUES ('bus_service', 'INSERT', CONCAT(NEW.service_no, '/', NEW.direction),
            JSON_OBJECT('operator_code', NEW.operator_code, 'category', NEW.category,
                        'origin_code', NEW.origin_code, 'destination_code', NEW.destination_code),
            COALESCE(@app_user, USER()));
END$$

CREATE TRIGGER trg_bus_service_au AFTER UPDATE ON bus_service FOR EACH ROW
BEGIN
    INSERT INTO audit_log (table_name, action, row_key, old_data, new_data, changed_by)
    VALUES ('bus_service', 'UPDATE', CONCAT(NEW.service_no, '/', NEW.direction),
            JSON_OBJECT('operator_code', OLD.operator_code, 'category', OLD.category,
                        'am_peak', CONCAT_WS('-', OLD.am_peak_min, OLD.am_peak_max),
                        'pm_peak', CONCAT_WS('-', OLD.pm_peak_min, OLD.pm_peak_max)),
            JSON_OBJECT('operator_code', NEW.operator_code, 'category', NEW.category,
                        'am_peak', CONCAT_WS('-', NEW.am_peak_min, NEW.am_peak_max),
                        'pm_peak', CONCAT_WS('-', NEW.pm_peak_min, NEW.pm_peak_max)),
            COALESCE(@app_user, USER()));
END$$

CREATE TRIGGER trg_bus_service_ad AFTER DELETE ON bus_service FOR EACH ROW
BEGIN
    INSERT INTO audit_log (table_name, action, row_key, old_data, changed_by)
    VALUES ('bus_service', 'DELETE', CONCAT(OLD.service_no, '/', OLD.direction),
            JSON_OBJECT('operator_code', OLD.operator_code, 'category', OLD.category),
            COALESCE(@app_user, USER()));
END$$

CREATE TRIGGER trg_route_stop_ai AFTER INSERT ON route_stop FOR EACH ROW
BEGIN
    INSERT INTO audit_log (table_name, action, row_key, new_data, changed_by)
    VALUES ('route_stop', 'INSERT', CONCAT(NEW.service_no, '/', NEW.direction, '/', NEW.stop_sequence),
            JSON_OBJECT('bus_stop_code', NEW.bus_stop_code, 'distance_km', NEW.distance_km),
            COALESCE(@app_user, USER()));
END$$

CREATE TRIGGER trg_route_stop_au AFTER UPDATE ON route_stop FOR EACH ROW
BEGIN
    INSERT INTO audit_log (table_name, action, row_key, old_data, new_data, changed_by)
    VALUES ('route_stop', 'UPDATE', CONCAT(NEW.service_no, '/', NEW.direction, '/', NEW.stop_sequence),
            JSON_OBJECT('stop_sequence', OLD.stop_sequence, 'bus_stop_code', OLD.bus_stop_code,
                        'distance_km', OLD.distance_km),
            JSON_OBJECT('stop_sequence', NEW.stop_sequence, 'bus_stop_code', NEW.bus_stop_code,
                        'distance_km', NEW.distance_km),
            COALESCE(@app_user, USER()));
END$$

CREATE TRIGGER trg_route_stop_ad AFTER DELETE ON route_stop FOR EACH ROW
BEGIN
    INSERT INTO audit_log (table_name, action, row_key, old_data, changed_by)
    VALUES ('route_stop', 'DELETE', CONCAT(OLD.service_no, '/', OLD.direction, '/', OLD.stop_sequence),
            JSON_OBJECT('bus_stop_code', OLD.bus_stop_code, 'distance_km', OLD.distance_km),
            COALESCE(@app_user, USER()));
END$$

-- Non-standard integrity constraint: a user may save at most 20 favourite stops.
CREATE TRIGGER trg_favourite_limit BEFORE INSERT ON favourite_stop FOR EACH ROW
BEGIN
    IF (SELECT COUNT(*) FROM favourite_stop WHERE user_id = NEW.user_id) >= 20 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A user can save at most 20 favourite stops';
    END IF;
END$$

DELIMITER ;
