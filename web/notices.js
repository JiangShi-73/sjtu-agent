import { collegeNames } from './academic.js';
import { collegeBulletins } from './college-bulletins.js';
import { campusNotices } from './public-data.js';

export function noticesForCollege(collegeId) {
  const curated = campusNotices.filter((notice) => !notice.collegeId || notice.openAudience || notice.collegeId === collegeId);
  const feed = Object.hasOwn(collegeBulletins, collegeId) ? collegeBulletins[collegeId] : null;
  const canonical = (url) => url.replace(/^https:\/\/www\./, 'https://');
  const curatedUrls = new Set(curated.map(({ url }) => url && canonical(url)));
  const collected = (feed?.items ?? []).filter(({ url }) => !curatedUrls.has(canonical(url)))
    .map((notice) => ({ ...notice, collegeId, publisher: collegeNames[collegeId] }));
  return [...curated, ...collected];
}
