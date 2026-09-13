-- Pulse AI Alert Subscriptions — Table + Indexes
-- Apply this in the Supabase SQL Editor (Dashboard → SQL Editor → New Query)

CREATE TABLE alert_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL,
    criteria_type TEXT NOT NULL CHECK (criteria_type IN (
        'spike', 'rate_change', 'price_threshold', 'availability_change',
        'new_listing', 'tracking_removed', 'anomaly', 'volatility',
        'trend_reversal', 'digest'
    )),
    criteria JSONB NOT NULL,
    raw_request_text TEXT,
    confirmed BOOLEAN NOT NULL DEFAULT false,
    confirmation_token TEXT NOT NULL UNIQUE,
    unsubscribe_token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at TIMESTAMPTZ,
    last_checked_at TIMESTAMPTZ,
    last_fired_at TIMESTAMPTZ
);

CREATE INDEX idx_alert_subs_status ON alert_subscriptions(status) WHERE status = 'active';
CREATE INDEX idx_alert_subs_email ON alert_subscriptions(email);
CREATE INDEX idx_alert_subs_token ON alert_subscriptions(confirmation_token);
CREATE INDEX idx_alert_subs_unsub ON alert_subscriptions(unsubscribe_token);
