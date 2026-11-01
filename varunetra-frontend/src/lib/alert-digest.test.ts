import { describe, it, expect } from 'vitest';
import {
  buildAlertBrief, buildThreads, classify, groupStatements, speakerOf, timeAgo, type DigestReport,
} from './alert-digest';

const NOW = Date.parse('2026-09-17T12:00:00Z');
let seq = 0;
const report = (title: string, extra: Partial<DigestReport> = {}): DigestReport => ({
  id: `r${++seq}`,
  title,
  source: 't.me/a',
  source_name: 'A',
  bloc: 'western',
  published: '2026-09-17T11:00:00Z',
  ...extra,
});

describe('classify', () => {
  it('files a report under every theatre and topic it names', () => {
    const c = classify({ title: 'Iranian drones struck a tanker in the Strait of Hormuz; Israel on alert' });
    expect(c.theatres).toEqual(expect.arrayContaining(['iran-gulf', 'israel-gaza-lebanon']));
    expect(c.topics).toEqual(expect.arrayContaining(['drones', 'strikes', 'maritime']));
  });

  /* Palestinian and Lebanese newsrooms name the town, not the country. */
  it('files town-level Gaza, West Bank and south Lebanon reporting under the right theatre', () => {
    for (const title of [
      'Israeli occupation forces raid Jenin refugee camp',
      'Casualties reported after a strike on Khan Younis',
      'Shelling near Nabatieh as UNIFIL reports movement along the Blue Line',
      'Clashes in Umm al-Fahm after a funeral procession',
    ]) {
      expect(classify({ title }).theatres, title).toContain('israel-gaza-lebanon');
    }
  });

  it('reads Russian-language posts', () => {
    const c = classify({ title: 'В Кремле переполох — принятие закона об «адских санкциях»' });
    expect(c.theatres).toContain('russia-ukraine');
    expect(c.topics).toContain('sanctions');
  });

  it('matches word starts and whole words, not fragments', () => {
    // "Mali" must not fire on "malicious", nor "port" on "report".
    expect(classify({ title: 'Researchers report a malicious campaign' })).toEqual({ theatres: [], topics: [] });
    expect(classify({ title: 'Ukrainian forces advance' }).theatres).toEqual(['russia-ukraine']);
  });
});

describe('buildThreads', () => {
  it('ranks theatres by volume and names the channels carrying them', () => {
    const threads = buildThreads([
      report('Drone attack on Kharkiv overnight', { source_name: 'Liveuamap' }),
      report('Russian forces advance near Pokrovsk', { source_name: 'Rybar', source: 't.me/r', bloc: 'russian' }),
      report('Israeli strikes reported in southern Lebanon'),
    ]);
    expect(threads.map(t => [t.id, t.count])).toEqual([['russia-ukraine', 2], ['israel-gaza-lebanon', 1]]);
    expect(threads[0].sources).toEqual(['Liveuamap', 'Rybar']);
  });

  it('marks a story carried by opposing sides as cross-perspective', () => {
    const [t] = buildThreads([
      report('Explosions in Kherson', {
        also_reported_by: [{ source: 't.me/r', source_name: 'Rybar', bloc: 'russian' }],
      }),
    ]);
    expect(t.perspective).toBe('cross');
    expect(t.blocs).toEqual({ western: 1, russian: 1 });
  });

  it('marks a theatre reported by one side only as single-perspective', () => {
    const [t] = buildThreads([
      report('Cauldron closing near Volchansk', { bloc: 'russian' }),
      report('Kupyansk assault continues', { bloc: 'russian' }),
    ]);
    expect(t.perspective).toBe('single');
  });

  it('leads with the most widely carried report', () => {
    const [t] = buildThreads([
      report('Kyiv power outage after strikes', { published: '2026-09-17T11:59:00Z' }),
      report('Zelensky meets Romanian president in Kyiv', {
        published: '2026-09-17T09:00:00Z',
        also_reported_by: [{ source: 't.me/b', bloc: 'western' }],
      }),
    ]);
    expect(t.lead?.title).toBe('Zelensky meets Romanian president in Kyiv');
    expect(t.latest).toBe('2026-09-17T11:59:00.000Z');
  });

  it('leaves out reports that name no tracked theatre', () => {
    expect(buildThreads([report('Local weather is mild today')])).toEqual([]);
  });
});

describe('buildAlertBrief', () => {
  it('states the leading theatre and its sourcing in the bottom line', () => {
    const brief = buildAlertBrief({
      news: [
        report('Drone attack on Kharkiv', { also_reported_by: [{ source: 't.me/r', bloc: 'russian' }] }),
        report('Strikes on Odesa port', { flag: 'BREAKING' }),
        report('Houthis claim missile launch at Red Sea shipping'),
      ],
      earthquakes: [
        { magnitude: 4.1, place: 'near Tonga', time: NOW - 3_600_000 },
        { magnitude: 6.2, place: '80 km S of Kuril', time: NOW - 7_200_000, tsunami: 1 },
      ],
    }, NOW);

    expect(brief.bottomLine).toBe(
      'Russia–Ukraine war leads the feed: 2 reports from 2 channels, carried by both Western and Russian-aligned channels.'
      + ' Also active: Yemen & Red Sea (1). Strongest quake: M6.2 80 km S of Kuril.',
    );
    expect(brief.coverage).toMatchObject({ reports: 3, breaking: 1, corroborated: 1 });
    expect(brief.seismic).toMatchObject({ count: 2, significant: 1, strongest: { magnitude: 6.2, tsunami: true } });
    expect(brief.facts.some(f => f.includes('tsunami flag set'))).toBe(true);
    expect(brief.highlights).toEqual(['Russia–Ukraine war · 2', 'Yemen & Red Sea · 1', 'M6.2 quake', '1 breaking']);
    expect(brief.method).toMatch(/does not verify/);
  });

  it('says so plainly when the feed is empty', () => {
    const brief = buildAlertBrief({ news: [], earthquakes: [] }, NOW);
    expect(brief.bottomLine).toBe('No reports in the current feed window.');
    expect(brief.threads).toEqual([]);
    expect(brief.seismic).toBeNull();
  });

  it('tolerates missing fields from older payloads', () => {
    const brief = buildAlertBrief({ news: [{ id: 'x', title: 'Ceasefire talks in Doha', source: 'BBC' }] }, NOW);
    expect(brief.threads[0].id).toBe('iran-gulf');
    expect(brief.coverage.channels).toBe(1);
  });
});

describe('speakerOf', () => {
  it('reads the speaker label in front of a quote', () => {
    expect(speakerOf('Iranian TV: Crews of the two tankers are being transferred')).toBe('Iranian TV');
    expect(speakerOf('U.S. Ambassador to Israel Mike Huckabee: If something similar had happened')).toBe('U.S. Ambassador to Israel Mike Huckabee');
  });

  it('ignores colons that belong to a sentence', () => {
    expect(speakerOf('The ministry said this: talks resume Monday')).toBeNull();
    expect(speakerOf('Update at 14:00: shelling continues')).toBeNull();
    expect(speakerOf('No colon in this headline at all')).toBeNull();
  });
});

describe('groupStatements', () => {
  const at = (min: number) => NOW - min * 60_000;
  const q = (source: string, title: string, min: number) => ({ source, title, ts: at(min) });

  it('folds a run of quotes from one speaker on one channel', () => {
    const groups = groupStatements([
      q('clash', 'Khawaja Asif: Israel will be a big loser.', 1),
      q('other', 'Explosions reported in Odesa overnight', 2),
      q('clash', 'Khawaja Asif: Israel has exposed itself too much.', 3),
      q('clash', 'Khawaja Asif: Iran as a state is not supported.', 5),
    ]);
    expect(groups.map(g => [g.speaker, g.items.length])).toEqual([['Khawaja Asif', 3], [null, 1]]);
  });

  it('keeps the same speaker separate across channels and across long gaps', () => {
    const groups = groupStatements([
      q('clash', 'Iranian TV: first report', 1),
      q('liveuamap', 'Iranian TV: first report', 2),
      q('clash', 'Iranian TV: much later report', 600),
    ]);
    expect(groups).toHaveLength(3);
  });
});

describe('timeAgo', () => {
  it('formats relative ages', () => {
    expect(timeAgo(NOW - 20_000, NOW)).toBe('just now');
    expect(timeAgo(NOW - 12 * 60_000, NOW)).toBe('12m ago');
    expect(timeAgo(new Date(NOW - 3 * 3_600_000).toISOString(), NOW)).toBe('3h ago');
    expect(timeAgo(NOW - 50 * 3_600_000, NOW)).toBe('2d ago');
    expect(timeAgo(null, NOW)).toBe('');
  });
});
