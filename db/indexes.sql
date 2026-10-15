-- Indexes for idx_exchange.
--
-- The source phpMyAdmin dumps (rets_property.sql, california_sold.sql)
-- define columns only - no PRIMARY KEY, no secondary indexes, no FULLTEXT
-- index. On this project's live DB, rets_property already has a PRIMARY
-- KEY (id) plus indexes on L_ListingID, L_City, L_Zip, L_DisplayId,
-- L_Type_, and a FULLTEXT index on L_Remarks (ft_remarks) - added outside
-- this repo at some point, matching neither the dump nor this file's
-- original idx_* names. It is still missing indexes on L_Status and
-- L_SystemPrice, which every search_properties.py query filters/sorts on.
-- california_sold has no indexes at all.
--
-- This file is idempotent-ish via IF NOT EXISTS where supported; it only
-- adds what search_properties.py actually needs and does not duplicate
-- indexes already present under other names. Check `SHOW INDEX FROM
-- <table>;` before running on a DB you haven't inspected.
--
-- Run once:
--   mysql -u idx_user -p idx_exchange < db/indexes.sql

-- rets_property: WHERE L_Status = 'Active' / ORDER BY L_SystemPrice in
-- build_active_listing_query() - not covered by the existing indexes.
ALTER TABLE rets_property
  ADD INDEX idx_status (L_Status),
  ADD INDEX idx_price (L_SystemPrice);

-- california_sold: WHERE City = ? AND PropertyType = ? AND CloseDate >= ?
-- in build_sold_comps_query(), joined to rets_property via ListingKey.
ALTER TABLE california_sold
  ADD INDEX idx_city (City),
  ADD INDEX idx_close_date (CloseDate),
  ADD INDEX idx_property_type (PropertyType),
  ADD INDEX idx_listing_key (ListingKey);

-- Note: `id` in rets_property is already a PRIMARY KEY on this DB.
-- `ListingKey` in california_sold looks like a natural key but has no
-- uniqueness constraint here; verify before adding one:
--   SELECT ListingKey, COUNT(*) FROM california_sold GROUP BY ListingKey HAVING COUNT(*) > 1;
