/**
 * Job Radar → Google Sheet. Paste into Extensions → Apps Script of your sheet (see README).
 * Receives each run's jobs and updates the "Jobs" tab. Only the columns Job Radar owns are
 * written: Status, Notes and any columns you add are never touched on existing rows.
 */
const TAB = 'Jobs';
const HEADERS = ['Status', 'Score', 'Shortlist', 'Risk', 'Role', 'Company', 'Location', 'Salary', 'Type',
  'Matched skills', 'Why it fits', 'Watch out', 'Eligibility', 'Source', 'Posted', 'First seen', 'Last seen',
  'Notes', 'Key'];
const REFRESH = ['Score', 'Shortlist', 'Risk', 'Location', 'Salary', 'Type', 'Matched skills', 'Why it fits',
  'Watch out', 'Eligibility', 'Last seen'];  // rewritten on existing rows every run
const STATUSES = ['New', 'To apply', 'Applied', 'Interview', 'Offer', 'Rejected', 'Skip'];

/** Run this once from the editor: builds the tab and prints the token for GitHub. */
function setup() {
  const props = PropertiesService.getScriptProperties();
  if (!props.getProperty('TOKEN')) {
    props.setProperty('TOKEN', (Utilities.getUuid() + Utilities.getUuid()).replace(/-/g, ''));
  }
  props.setProperty('SHEET_ID', SpreadsheetApp.getActiveSpreadsheet().getId());
  jobsTab_();
  console.log('GOOGLE_SHEET_TOKEN: ' + props.getProperty('TOKEN'));
}

function doPost(e) {
  const reply = o => ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
  const lock = LockService.getScriptLock();
  try {
    const body = JSON.parse(e.postData.contents);
    const token = PropertiesService.getScriptProperties().getProperty('TOKEN');
    if (!token || body.token !== token) return reply({ok: false, error: 'wrong token'});
    lock.waitLock(30000);
    return reply(Object.assign({ok: true}, update_(body.date, body.jobs)));
  } catch (err) {
    return reply({ok: false, error: String(err)});
  } finally {
    lock.releaseLock();
  }
}

function jobsTab_() {
  const ss = SpreadsheetApp.openById(PropertiesService.getScriptProperties().getProperty('SHEET_ID'));
  let sh = ss.getSheetByName(TAB);
  if (!sh) sh = ss.insertSheet(TAB, 0);
  if (sh.getLastRow() === 0) {
    sh.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]).setFontWeight('bold').setBackground('#eeeeea');
    sh.setFrozenRows(1);
    const widths = {Role: 320, Company: 160, Location: 180, 'Matched skills': 260, 'Why it fits': 240,
      'Watch out': 240, Notes: 240};
    HEADERS.forEach((h, i) => sh.setColumnWidth(i + 1, widths[h] || 100));
    sh.hideColumns(HEADERS.indexOf('Key') + 1);
  }
  return sh;
}

// Listing text comes from the web. A leading = + - @ would run as a formula, so force it to text.
function safe_(v) {
  return typeof v === 'string' && /^[=+\-@]/.test(v) ? "'" + v : v;
}

function update_(date, jobs) {
  const sh = jobsTab_();
  const head = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0];
  const col = {};  // by name, so you can reorder or delete columns
  head.forEach((h, i) => { if (h) col[h] = i + 1; });
  if (!col.Key) throw new Error('The hidden "Key" column is missing from the Jobs tab');

  const byKey = {};
  jobs.forEach(j => { byKey[j.Key] = j; });
  const n = sh.getLastRow() - 1;
  const known = new Set();
  if (n > 0) {
    const keys = sh.getRange(2, col.Key, n, 1).getValues().map(r => String(r[0]));
    keys.forEach(k => known.add(k));
    REFRESH.filter(h => col[h]).forEach(h => {
      const range = sh.getRange(2, col[h], n, 1);
      range.setValues(range.getValues().map((r, i) => {
        const j = byKey[keys[i]];
        if (j) return [safe_(h === 'Last seen' ? date : j[h])];
        return [h === 'Shortlist' && keys[i] ? '' : safe_(r[0])];  // not listed today: off the shortlist
      }));
    });
  }

  const fresh = Object.values(byKey).filter(j => !known.has(j.Key));  // newest go on top, best score first
  if (fresh.length) {
    sh.insertRowsAfter(1, fresh.length);
    const rows = fresh.map(j => head.map(h => {
      if (h === 'Status') return 'New';
      if (h === 'First seen' || h === 'Last seen') return date;
      return h in j ? safe_(j[h]) : '';
    }));
    const range = sh.getRange(2, 1, fresh.length, head.length);
    range.setFontWeight('normal').setBackground(null).setVerticalAlignment('top').setValues(rows);
    if (col.Role) {
      sh.getRange(2, col.Role, fresh.length, 1).setRichTextValues(fresh.map(j => {
        const t = SpreadsheetApp.newRichTextValue().setText(j.Role);
        if (/^https?:\/\//i.test(j.url)) t.setLinkUrl(j.url);
        return [t.build()];
      }));
    }
    if (col.Status) {
      sh.getRange(2, col.Status, fresh.length, 1).setDataValidation(
        SpreadsheetApp.newDataValidation().requireValueInList(STATUSES, true).setAllowInvalid(true).build());
    }
  }
  if (!sh.getFilter()) sh.getDataRange().createFilter();
  return {added: fresh.length, updated: jobs.length - fresh.length};
}
