// googleNewsFetcher.test.js — the parsing and filtering logic.
//
// Node's built-in test runner (`npm test` → `node --test`), no framework.
//
// These target the pure exports rather than `fetchStrategicNews`, which needs
// Redis and network. The parsing is where the risk actually is: the input is a
// third-party XML document whose shape is not a contract we control, and every
// failure mode here is silent — a bad regex yields zero articles, the analyzer
// takes its no-news path, and the panel reads "Neutral / 0 headlines" while the
// service reports perfect health. That is exactly the state this whole change was
// made to escape, so it gets tests.
//
// The XML fixtures below are trimmed from a live capture of
// news.google.com/rss/search, including the quirks that surprised me: the
// `<description>` is an anchor tag rather than a summary, and titles carry a
// trailing " - Publisher".

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  buildQuery,
  cleanTitle,
  decodeEntities,
  isRecent,
  parseRssItems,
} from '../src/googleNewsFetcher.js';

// ── buildQuery ───────────────────────────────────────────────────────────────

test('buildQuery pairs the quoted company name with the bucket terms and a recency bound', () => {
  const q = buildQuery(
    { category: 'EARNINGS', terms: 'earnings OR profit' },
    'Reliance Industries',
    null,
    7,
  );
  assert.equal(q, '"Reliance Industries" (earnings OR profit) when:7d');
});

test('buildQuery always includes when:Nd', () => {
  // Not cosmetic. Measured against the live feed: without it, an August query
  // returned June articles. Stale news scored as fresh sentiment is worse than no
  // news at all, because it reads as a live catalyst.
  const q = buildQuery({ category: 'X', terms: 'a OR b' }, 'Infosys', null, 2);
  assert.match(q, /when:2d$/);
});

test('buildQuery uses only the first sector token for the macro bucket', () => {
  const q = buildQuery(
    { category: 'SECTOR_MACRO', terms: '', sectorBased: true },
    'Reliance Industries',
    'Energy / Conglomerate',
    7,
  );
  assert.equal(q, '"Reliance Industries" Energy when:7d');
});

test('buildQuery returns null for the macro bucket with no sector', () => {
  // A clean skip, not a failure — the caller distinguishes the two in metrics.
  const bucket = { category: 'SECTOR_MACRO', terms: '', sectorBased: true };
  assert.equal(buildQuery(bucket, 'Infosys', null, 7), null);
  assert.equal(buildQuery(bucket, 'Infosys', '   ', 7), null);
  assert.equal(buildQuery(bucket, 'Infosys', ' / ', 7), null);
});

test('buildQuery returns null rather than querying for a blank company name', () => {
  // A bare `(terms) when:7d` query would return unrelated market-wide news and
  // score it as though it were about this symbol.
  assert.equal(buildQuery({ category: 'X', terms: 'earnings' }, '', null, 7), null);
  assert.equal(buildQuery({ category: 'X', terms: 'earnings' }, '   ', null, 7), null);
  assert.equal(buildQuery({ category: 'X', terms: '' }, 'Infosys', null, 7), null);
});

// ── cleanTitle ───────────────────────────────────────────────────────────────

test('cleanTitle strips the publisher suffix Google appends', () => {
  assert.equal(
    cleanTitle('Reliance Q1 profit rises 12% - The Economic Times', 'The Economic Times'),
    'Reliance Q1 profit rises 12%',
  );
});

test('cleanTitle keeps dashes that belong to the headline', () => {
  // The regression this pins: an earlier "looks like a publisher" fallback turned
  // this into just "Reliance". Only an exact <source> match is stripped now, so a
  // headline containing a dash survives intact.
  assert.equal(
    cleanTitle('Reliance - Jio merger talks advance', null),
    'Reliance - Jio merger talks advance',
  );
  // Even WITH a source, a non-matching suffix is left alone.
  assert.equal(
    cleanTitle('Reliance - Jio merger talks advance', 'Mint'),
    'Reliance - Jio merger talks advance',
  );
});

test('cleanTitle leaves the title alone when no source is given', () => {
  // Measured on a live 100-item feed: every item carried <source> and every title
  // ended in " - <source>". So there is no case this fallback would have served,
  // and guessing can only destroy real headlines.
  assert.equal(cleanTitle('Infosys wins $1bn deal - Reuters', null), 'Infosys wins $1bn deal - Reuters');
  assert.equal(cleanTitle('Infosys wins $1bn deal - Reuters', 'Reuters'), 'Infosys wins $1bn deal');
});

test('cleanTitle leaves a sentence-like tail alone', () => {
  const t = 'Board meets today - a decision is expected.';
  assert.equal(cleanTitle(t, null), t);
});

test('cleanTitle is total on empty input', () => {
  assert.equal(cleanTitle('', null), '');
  assert.equal(cleanTitle(null, null), '');
  assert.equal(cleanTitle(undefined, 'X'), '');
});

// ── decodeEntities ───────────────────────────────────────────────────────────

test('decodeEntities decodes ampersands last so tags cannot be forged', () => {
  // `&amp;lt;` must become the literal text `&lt;`, NOT a `<`. Decoding `&amp;`
  // first would produce `&lt;` and then `<`, letting encoded input become markup.
  assert.equal(decodeEntities('&amp;lt;script&amp;gt;'), '&lt;script&gt;');
  assert.equal(decodeEntities('Tata &amp; Sons'), 'Tata & Sons');
  assert.equal(decodeEntities('&quot;quoted&quot; &#39;apos&#39;'), '"quoted" \'apos\'');
});

// ── parseRssItems ────────────────────────────────────────────────────────────

const FEED = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>News</title>
<item>
  <title>Reliance Q1 profit rises 12% on retail strength - The Economic Times</title>
  <link>https://news.google.com/rss/articles/AAA?oc=5</link>
  <guid isPermaLink="false">AAA</guid>
  <pubDate>Fri, 21 Aug 2026 08:54:10 GMT</pubDate>
  <description>&lt;a href="https://news.google.com/rss/articles/AAA"&gt;Reliance Q1&lt;/a&gt;</description>
  <source url="https://economictimes.indiatimes.com">The Economic Times</source>
</item>
<item>
  <title>SEBI opens probe into Tata &amp; Sons unit - Mint</title>
  <link>https://news.google.com/rss/articles/BBB?oc=5</link>
  <guid isPermaLink="false">BBB</guid>
  <pubDate>Thu, 20 Aug 2026 11:10:43 GMT</pubDate>
  <source url="https://livemint.com">Mint</source>
</item>
</channel></rss>`;

test('parseRssItems extracts the fields the analyzer needs', () => {
  const items = parseRssItems(FEED);
  assert.equal(items.length, 2);

  assert.deepEqual(items[0], {
    title: 'Reliance Q1 profit rises 12% on retail strength',
    url: 'https://news.google.com/rss/articles/AAA?oc=5',
    published_at: 'Fri, 21 Aug 2026 08:54:10 GMT',
    sourceName: 'The Economic Times',
    guid: 'AAA',
  });
});

test('parseRssItems decodes entities in titles', () => {
  const items = parseRssItems(FEED);
  assert.equal(items[1].title, 'SEBI opens probe into Tata & Sons unit');
});

test('parseRssItems drops an item with no title or no link', () => {
  // Admitting a half-built item would put an empty headline in front of the user
  // or hand the analyzer a URL it cannot attribute.
  const xml = `<rss><channel>
    <item><link>https://x/1</link><guid>1</guid></item>
    <item><title>Has title, no link</title><guid>2</guid></item>
    <item><title>Good one</title><link>https://x/3</link><guid>3</guid></item>
  </channel></rss>`;
  const items = parseRssItems(xml);
  assert.equal(items.length, 1);
  assert.equal(items[0].title, 'Good one');
});

test('parseRssItems unwraps CDATA', () => {
  const xml = `<rss><channel><item>
    <title><![CDATA[Infosys raises guidance]]></title>
    <link><![CDATA[https://x/1]]></link>
    <guid>1</guid>
  </item></channel></rss>`;
  const items = parseRssItems(xml);
  assert.equal(items[0].title, 'Infosys raises guidance');
  assert.equal(items[0].url, 'https://x/1');
});

test('parseRssItems returns [] for junk rather than throwing', () => {
  // The service polls in a loop; a throw here would take down the cycle for every
  // symbol, so malformed input has to degrade to "no news".
  for (const junk of ['', '   ', 'not xml at all', '<rss><channel></channel></rss>', null, undefined]) {
    assert.deepEqual(parseRssItems(junk), []);
  }
});

test('parseRssItems does not confuse the channel title for an item title', () => {
  // The `<channel><title>` sits outside any `<item>`; a lazier regex would grab it
  // and emit a phantom headline.
  const items = parseRssItems(FEED);
  assert.ok(!items.some((i) => i.title === 'News'));
});

// ── isRecent ─────────────────────────────────────────────────────────────────

const NOW = Date.parse('Fri, 21 Aug 2026 12:00:00 GMT');

test('isRecent accepts an item inside the window', () => {
  assert.equal(isRecent('Fri, 21 Aug 2026 08:00:00 GMT', NOW), true);
  assert.equal(isRecent('Mon, 17 Aug 2026 08:00:00 GMT', NOW), true);
});

test('isRecent rejects a genuinely stale item', () => {
  // The exact failure measured on the live feed: June articles in an August query.
  assert.equal(isRecent('Fri, 19 Jun 2026 07:00:00 GMT', NOW), false);
});

test('isRecent admits an item with a missing or unparseable date', () => {
  // It came back from a `when:Nd` query, so Google already judged it recent.
  // Dropping real news over a date-format quirk is the worse error.
  assert.equal(isRecent('', NOW), true);
  assert.equal(isRecent('not a date', NOW), true);
});

test('isRecent tolerates the window edge', () => {
  // A day of slack over the query window, so timezone rounding does not discard a
  // legitimately-fresh item.
  const sevenDaysAgo = NOW - 7 * 24 * 60 * 60 * 1000;
  assert.equal(isRecent(new Date(sevenDaysAgo).toUTCString(), NOW), true);
  const tenDaysAgo = NOW - 10 * 24 * 60 * 60 * 1000;
  assert.equal(isRecent(new Date(tenDaysAgo).toUTCString(), NOW), false);
});
