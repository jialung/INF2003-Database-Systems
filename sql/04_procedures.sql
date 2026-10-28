USE transport;

DROP PROCEDURE IF EXISTS sp_insert_route_stop;
DROP PROCEDURE IF EXISTS sp_delete_route_stop;

DELIMITER $$

-- Inserts a stop into a route at position p_seq, shifting later stops down by one.
-- The shift and the insert run in one transaction, so a failure leaves the route untouched.
CREATE PROCEDURE sp_insert_route_stop(
    IN p_service  VARCHAR(5),
    IN p_dir      TINYINT,
    IN p_seq      SMALLINT,
    IN p_stop     CHAR(5),
    IN p_distance DECIMAL(5,1)
)
BEGIN
    DECLARE v_max_seq  SMALLINT;
    DECLARE v_prev_km  DECIMAL(5,1);
    DECLARE v_next_km  DECIMAL(5,1);

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    IF NOT EXISTS (SELECT 1 FROM bus_service WHERE service_no = p_service AND direction = p_dir) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown service or direction';
    END IF;

    -- Lock the route's rows so two admins cannot renumber it at the same time.
    SELECT COALESCE(MAX(stop_sequence), 0) INTO v_max_seq
    FROM route_stop WHERE service_no = p_service AND direction = p_dir FOR UPDATE;

    IF p_seq < 1 OR p_seq > v_max_seq + 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Stop sequence is outside the route';
    END IF;

    -- Distance is cumulative from the origin, so it must sit between its neighbours.
    SELECT distance_km INTO v_prev_km FROM route_stop
    WHERE service_no = p_service AND direction = p_dir AND stop_sequence = p_seq - 1;
    SELECT distance_km INTO v_next_km FROM route_stop
    WHERE service_no = p_service AND direction = p_dir AND stop_sequence = p_seq;

    IF (v_prev_km IS NOT NULL AND p_distance < v_prev_km)
       OR (v_next_km IS NOT NULL AND p_distance > v_next_km) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Distance must lie between the neighbouring stops';
    END IF;

    -- Shift from the end backwards so no intermediate state violates the primary key.
    UPDATE route_stop SET stop_sequence = stop_sequence + 1
    WHERE service_no = p_service AND direction = p_dir AND stop_sequence >= p_seq
    ORDER BY stop_sequence DESC;

    INSERT INTO route_stop (service_no, direction, stop_sequence, bus_stop_code, distance_km)
    VALUES (p_service, p_dir, p_seq, p_stop, p_distance);

    COMMIT;
END$$

-- Removes the stop at position p_seq and closes the gap.
CREATE PROCEDURE sp_delete_route_stop(
    IN p_service VARCHAR(5),
    IN p_dir     TINYINT,
    IN p_seq     SMALLINT
)
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    DELETE FROM route_stop
    WHERE service_no = p_service AND direction = p_dir AND stop_sequence = p_seq;

    IF ROW_COUNT() = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'No stop at that position';
    END IF;

    UPDATE route_stop SET stop_sequence = stop_sequence - 1
    WHERE service_no = p_service AND direction = p_dir AND stop_sequence > p_seq
    ORDER BY stop_sequence ASC;

    COMMIT;
END$$

DELIMITER ;
