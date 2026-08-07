-- =============================================================================
-- LoyalCorp P1 Bot — PostgreSQL Schema
-- =============================================================================

DROP TABLE IF EXISTS calls CASCADE;
DROP TABLE IF EXISTS campaign_data CASCADE;
DROP TABLE IF EXISTS campaigns CASCADE;
DROP TABLE IF EXISTS lead_numbers CASCADE;
DROP TABLE IF EXISTS leads CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS bot_settings CASCADE;
DROP TABLE IF EXISTS users CASCADE;

-- =============================================================================
-- Users
-- =============================================================================
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    telegram_id     BIGINT UNIQUE NOT NULL,
    username        VARCHAR(255),
    first_name      VARCHAR(255),
    last_name       VARCHAR(255),
    credits         DECIMAL(12, 4) DEFAULT 0.0000,
    total_spent     DECIMAL(12, 4) DEFAULT 0.0000,
    is_active       BOOLEAN DEFAULT FALSE,      -- FALSE = awaiting admin approval
    is_banned       BOOLEAN DEFAULT FALSE,
    ban_reason      VARCHAR(500),
    p1_group_id     BIGINT,                     -- Optional Telegram group for P1 alerts
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_telegram_id ON users(telegram_id);
CREATE INDEX idx_users_is_active   ON users(is_active);

-- =============================================================================
-- Lead Lists (per user)
-- =============================================================================
CREATE TABLE leads (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    list_name       VARCHAR(255) NOT NULL,
    total_numbers   INTEGER DEFAULT 0,
    available_numbers INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, list_name)
);

CREATE INDEX idx_leads_user_id ON leads(user_id);

-- =============================================================================
-- Lead Numbers
-- =============================================================================
CREATE TABLE lead_numbers (
    id              SERIAL PRIMARY KEY,
    lead_id         INTEGER REFERENCES leads(id) ON DELETE CASCADE,
    phone_number    VARCHAR(20) NOT NULL,
    status          VARCHAR(20) DEFAULT 'available',   -- available, used
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_lead_numbers_lead_id ON lead_numbers(lead_id);
CREATE INDEX idx_lead_numbers_status  ON lead_numbers(status);

-- =============================================================================
-- Payments (Oxapay)
-- =============================================================================
CREATE TABLE payments (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    track_id        VARCHAR(255) UNIQUE,
    order_id        VARCHAR(255),
    amount          DECIMAL(12, 4) NOT NULL,
    credits         DECIMAL(12, 4),
    currency        VARCHAR(10) DEFAULT 'USDT',
    status          VARCHAR(20) DEFAULT 'pending',    -- pending, confirmed, failed
    payment_url     TEXT,
    tx_hash         VARCHAR(255),
    callback_data   JSONB,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    confirmed_at    TIMESTAMP
);

CREATE INDEX idx_payments_user_id  ON payments(user_id);
CREATE INDEX idx_payments_track_id ON payments(track_id);
CREATE INDEX idx_payments_status   ON payments(status);

-- =============================================================================
-- Campaigns
-- =============================================================================
CREATE TABLE campaigns (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    lead_id         INTEGER REFERENCES leads(id),
    name            VARCHAR(255) NOT NULL,
    caller_id       VARCHAR(20),
    cps             INTEGER DEFAULT 5,
    total_numbers   INTEGER DEFAULT 0,
    completed       INTEGER DEFAULT 0,
    answered        INTEGER DEFAULT 0,
    pressed_one     INTEGER DEFAULT 0,
    failed          INTEGER DEFAULT 0,
    status          VARCHAR(20) DEFAULT 'draft',   -- draft, running, paused, completed
    voice_file      VARCHAR(500),   -- filename in /var/lib/asterisk/sounds/
    outro_file      VARCHAR(500),
    cost_per_1k     DECIMAL(10, 4) DEFAULT 5.0,   -- Price at campaign creation time
    actual_cost     DECIMAL(12, 4) DEFAULT 0.0000,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP
);

CREATE INDEX idx_campaigns_user_id ON campaigns(user_id);
CREATE INDEX idx_campaigns_status  ON campaigns(status);

-- =============================================================================
-- Campaign Data (numbers copied at start)
-- =============================================================================
CREATE TABLE campaign_data (
    id              SERIAL PRIMARY KEY,
    campaign_id     INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
    lead_number_id  INTEGER REFERENCES lead_numbers(id),
    phone_number    VARCHAR(20) NOT NULL,
    status          VARCHAR(20) DEFAULT 'pending',   -- pending, dialing, answered, failed, completed
    call_id         VARCHAR(255),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    called_at       TIMESTAMP
);

CREATE INDEX idx_campaign_data_campaign_id ON campaign_data(campaign_id);
CREATE INDEX idx_campaign_data_status      ON campaign_data(status);
CREATE INDEX idx_campaign_data_call_id     ON campaign_data(call_id);

-- =============================================================================
-- Call Detail Records
-- =============================================================================
CREATE TABLE calls (
    id              SERIAL PRIMARY KEY,
    campaign_id     INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
    campaign_data_id INTEGER REFERENCES campaign_data(id) ON DELETE CASCADE,
    call_id         VARCHAR(255) UNIQUE,
    phone_number    VARCHAR(20) NOT NULL,
    caller_id       VARCHAR(20),
    status          VARCHAR(30),     -- ANSWER, BUSY, NO ANSWER, FAILED, COMPLETED, MACHINE
    dtmf_pressed    INTEGER DEFAULT 0,
    duration        INTEGER DEFAULT 0,
    billsec         INTEGER DEFAULT 0,
    cost            DECIMAL(10, 4) DEFAULT 0.0,
    hangup_cause    VARCHAR(100),
    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    answered_at     TIMESTAMP,
    ended_at        TIMESTAMP
);

CREATE INDEX idx_calls_campaign_id     ON calls(campaign_id);
CREATE INDEX idx_calls_call_id         ON calls(call_id);
CREATE INDEX idx_calls_status          ON calls(status);

-- =============================================================================
-- Bot Settings (admin-configurable at runtime)
-- =============================================================================
CREATE TABLE bot_settings (
    key             VARCHAR(100) PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Default settings
INSERT INTO bot_settings (key, value) VALUES
    ('price_per_1k', '5.00'),
    ('min_topup', '10.00'),
    ('max_cps', '50');