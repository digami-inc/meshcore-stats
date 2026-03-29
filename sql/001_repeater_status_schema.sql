CREATE TABLE `repeater_neighbors_history` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `collected_ts` datetime NOT NULL,
  `repeater_node` varchar(32) NOT NULL,
  `neighbor_pubkey_pre` varchar(32) NOT NULL,
  `neighbor_name` varchar(255) DEFAULT NULL,
  `neighbor_seen_ts` bigint(20) unsigned NOT NULL,
  `snr_x4` int(11) NOT NULL,
  `snr_db` decimal(6,2) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_rnh_rep_ts` (`repeater_node`,`collected_ts`),
  KEY `idx_rnh_nbr_ts` (`neighbor_pubkey_pre`,`collected_ts`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;
CREATE TABLE `repeater_status_history` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `ts` datetime NOT NULL,
  `node` varchar(32) NOT NULL,
  `bat_mv` int(11) DEFAULT NULL,
  `noise_floor_dbm` smallint(6) DEFAULT NULL,
  `last_rssi_dbm` smallint(6) DEFAULT NULL,
  `last_snr_db` decimal(5,2) DEFAULT NULL,
  `tx_queue_len` int(11) DEFAULT NULL,
  `nb_recv` bigint(20) DEFAULT NULL,
  `nb_sent` bigint(20) DEFAULT NULL,
  `airtime_secs` bigint(20) DEFAULT NULL,
  `rx_airtime_secs` bigint(20) DEFAULT NULL,
  `uptime_secs` bigint(20) DEFAULT NULL,
  `sent_flood` bigint(20) DEFAULT NULL,
  `sent_direct` bigint(20) DEFAULT NULL,
  `recv_flood` bigint(20) DEFAULT NULL,
  `recv_direct` bigint(20) DEFAULT NULL,
  `full_evts` bigint(20) DEFAULT NULL,
  `direct_dups` bigint(20) DEFAULT NULL,
  `flood_dups` bigint(20) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ts` (`ts`),
  KEY `idx_node_ts` (`node`,`ts`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE `repeater_status_meta` (
  `node` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `last_poll_started_ts` datetime DEFAULT NULL,
  `last_poll_finished_ts` datetime DEFAULT NULL,
  `next_poll_at` datetime DEFAULT NULL,
  `poll_state` varchar(16) DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`node`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;
CREATE TABLE `repeater_status_poll_log` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `ts_started` datetime NOT NULL,
  `ts_finished` datetime DEFAULT NULL,
  `node` varchar(64) NOT NULL,
  `is_valid` tinyint(1) NOT NULL DEFAULT 0,
  `status` varchar(16) NOT NULL,
  `error_text` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_node_started` (`node`,`ts_started`),
  KEY `idx_node_valid_started` (`node`,`is_valid`,`ts_started`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
