CREATE TABLE IF NOT EXISTS trial (device TEXT, slot TEXT, at REAL, PRIMARY KEY (device, slot));
CREATE TABLE IF NOT EXISTS keys (kid INTEGER PRIMARY KEY, plan INTEGER, device TEXT, expires REAL, customer TEXT, created REAL, revoked INTEGER DEFAULT 0, key TEXT);
CREATE TABLE IF NOT EXISTS seen (kid INTEGER, device TEXT, first REAL, last REAL, version TEXT, PRIMARY KEY (kid, device));
