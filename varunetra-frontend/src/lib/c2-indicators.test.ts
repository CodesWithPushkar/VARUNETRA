import { describe, it, expect } from 'vitest';
import { parseFeodoBlocklist, toIndicator, type FeodoRecord } from './c2-indicators';

/** The single-record fixture from the audit that reported the fabrication. */
const AUDIT_FIXTURE: FeodoRecord[] = [
  { ip_address: '192.0.2.10', country: 'US', malware: 'Emotet', dst_port: 443, status: 'offline' },
];

/** A row shaped the way the live dump publishes one. */
const LIVE_ROW: FeodoRecord = {
  ip_address: '198.51.100.7',
  port: 8080,
  status: 'online',
  hostname: 'c2.example.invalid',
  as_number: 64500,
  as_name: 'EXAMPLE-AS',
  as_country: 'de',
  first_seen: '2026-01-04 11:02:17',
  last_online: '2026-09-14',
  malware: 'QakBot',
};

describe('parseFeodoBlocklist', () => {
  it('reports one indicator per source record, not an arc target', () => {
    const indicators = parseFeodoBlocklist(AUDIT_FIXTURE);
    expect(indicators).toHaveLength(1);
  });

  it('asserts nothing the record does not contain', () => {
    const [indicator] = parseFeodoBlocklist(AUDIT_FIXTURE);
    // The record has no attacker, no victim, no attack verb and no severity.
    expect(indicator).not.toHaveProperty('action');
    expect(indicator).not.toHaveProperty('severity');
    expect(indicator).not.toHaveProperty('src_lat');
    expect(indicator).not.toHaveProperty('src_lng');
    expect(indicator).not.toHaveProperty('target_ip');
  });

  it('keeps the reported status rather than assuming an active attack', () => {
    const [indicator] = parseFeodoBlocklist(AUDIT_FIXTURE);
    expect(indicator.status).toBe('offline');
  });

  it('is deterministic across repeated parses of identical input', () => {
    expect(parseFeodoBlocklist(AUDIT_FIXTURE)).toEqual(parseFeodoBlocklist(AUDIT_FIXTURE));
    expect(parseFeodoBlocklist([LIVE_ROW])).toEqual(parseFeodoBlocklist([LIVE_ROW]));
  });

  it('deduplicates a C2 listed more than once', () => {
    const indicators = parseFeodoBlocklist([LIVE_ROW, { ...LIVE_ROW, malware: 'QakBot' }]);
    expect(indicators).toHaveLength(1);
    expect(indicators[0].id).toBe('198.51.100.7:8080');
  });

  it('carries the observation timestamps the record supplies', () => {
    const [indicator] = parseFeodoBlocklist([LIVE_ROW]);
    expect(indicator.first_seen).toBe('2026-01-04 11:02:17');
    expect(indicator.last_online).toBe('2026-09-14');
  });

  it('places a C2 at its hosting-country centroid and says so', () => {
    const [indicator] = parseFeodoBlocklist([LIVE_ROW]);
    expect(indicator.country).toBe('DE');
    expect(indicator.lng).toBe(10);
    expect(indicator.lat).toBe(51);
    expect(indicator.location_precision).toBe('country');
  });

  it('keeps a C2 with an unmappable country, without inventing a position', () => {
    const [indicator] = parseFeodoBlocklist([{ ...LIVE_ROW, as_country: 'ZZ' }]);
    expect(indicator.lng).toBeNull();
    expect(indicator.lat).toBeNull();
    expect(indicator.location_precision).toBeNull();
  });

  it('reads either port spelling and tolerates a missing one', () => {
    expect(toIndicator({ ip_address: '203.0.113.1', dst_port: '445' })?.port).toBe(445);
    expect(toIndicator({ ip_address: '203.0.113.1', port: 445 })?.port).toBe(445);
    expect(toIndicator({ ip_address: '203.0.113.1' })).toMatchObject({ port: null, id: '203.0.113.1' });
  });

  it('drops rows with no address and non-array payloads', () => {
    expect(parseFeodoBlocklist([{ malware: 'Emotet' }, null, 'nope'])).toEqual([]);
    expect(parseFeodoBlocklist({ attacks: [] })).toEqual([]);
  });
});
