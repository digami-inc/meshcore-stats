CREATE TABLE `meshcore_contact_current` (
  `source_id` varchar(255) NOT NULL,
  `contact_name` varchar(255) NOT NULL,
  `contact_name_norm` varchar(255) NOT NULL,
  `contact_type` int NOT NULL,
  `current_pubkey` varchar(128) NOT NULL,
  `current_pubkey_pre` varchar(32) NOT NULL,
  `first_seen_ts` datetime NOT NULL,
  `last_seen_ts` datetime NOT NULL,
  PRIMARY KEY (`source_id`,`contact_name_norm`),
  KEY `idx_mcc_pubkey` (`current_pubkey`(8)),
  KEY `idx_mcc_pubkey_pre` (`current_pubkey_pre`),
  KEY `idx_mcc_type` (`contact_type`),
  KEY `idx_mcc_last_seen` (`last_seen_ts`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
