#!/usr/bin/env node
// Plans the next release from the Conventional Commits since the last
// stable tag. A breaking change bumps major, feat bumps minor, and fix,
// perf or revert bump patch. Other types do not produce a release.
//
// Usage: node .github/scripts/release-plan.mjs [--to <ref>] [--notes <file>]
//                                               [--changelog <file>]
// Prints the plan as JSON and, inside GitHub Actions, writes the tag,
// version and sha outputs. The tag output is empty when nothing is due.
// --changelog adds the release to that file under today's date.

import { execFileSync } from 'node:child_process';
import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';

const args = process.argv.slice(2);
const option = (name) => {
  const index = args.indexOf(name);
  return index === -1 ? undefined : args[index + 1];
};
const to = option('--to') ?? 'HEAD';
const notesFile = option('--notes');
const changelogFile = option('--changelog');

const git = (...gitArgs) => execFileSync('git', gitArgs, { encoding: 'utf8' }).trim();

const STABLE_TAG = /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
const HEADER = /^(?<type>[a-z]+)(?:\((?<scope>[^)]+)\))?(?<bang>!)?: (?<description>.+)$/;
const BREAKING_FOOTER = /^BREAKING[ -]CHANGE: /m;
const SECTIONS = [
  ['breaking', 'Breaking Changes'],
  ['feat', 'Features'],
  ['fix', 'Bug Fixes'],
  ['perf', 'Performance'],
  ['revert', 'Reverts'],
];

const sha = git('rev-parse', `${to}^{commit}`);

// The newest stable tag reachable from the target is the previous release.
const previousTag = git('tag', '--merged', sha, '--sort=-v:refname')
  .split('\n')
  .find((tag) => STABLE_TAG.test(tag));
if (!previousTag) {
  throw new Error(`No stable v-prefixed tag is reachable from ${to}.`);
}

const SEPARATOR = '\x1e';
const log = git('log', '--no-merges', `--format=%H%x1f%B${SEPARATOR}`, `${previousTag}..${sha}`);
const commits = log
  .split(SEPARATOR)
  .map((entry) => entry.trim())
  .filter(Boolean)
  .map((entry) => {
    const [hash, message] = entry.split('\x1f');
    const [header, ...body] = message.split('\n');
    const match = HEADER.exec(header.trim());
    if (!match) return null;
    const { type, scope, bang, description } = match.groups;
    const breaking = Boolean(bang) || BREAKING_FOOTER.test(body.join('\n'));
    return { hash, type, scope, description, breaking };
  })
  .filter(Boolean)
  .reverse();

const releasable = commits.filter(
  (commit) => commit.breaking || ['feat', 'fix', 'perf', 'revert'].includes(commit.type),
);

let bump = null;
if (releasable.some((commit) => commit.breaking)) bump = 'major';
else if (releasable.some((commit) => commit.type === 'feat')) bump = 'minor';
else if (releasable.length > 0) bump = 'patch';

let [major, minor, patch] = STABLE_TAG.exec(previousTag).slice(1).map(Number);
if (bump === 'major') [major, minor, patch] = [major + 1, 0, 0];
if (bump === 'minor') [minor, patch] = [minor + 1, 0];
if (bump === 'patch') patch += 1;
const version = bump ? `${major}.${minor}.${patch}` : '';
const tag = bump ? `v${version}` : '';

const server = process.env.GITHUB_SERVER_URL ?? 'https://github.com';
const repository = process.env.GITHUB_REPOSITORY ?? 'lukasganster/immich-recompress';
const repoUrl = `${server}/${repository}`;

const line = (commit) => {
  const scope = commit.scope ? `**${commit.scope}:** ` : '';
  const short = commit.hash.slice(0, 7);
  return `* ${scope}${commit.description} ([${short}](${repoUrl}/commit/${commit.hash}))`;
};

const sections = [];
if (bump) {
  for (const [key, title] of SECTIONS) {
    const entries = releasable.filter((commit) =>
      key === 'breaking' ? commit.breaking : !commit.breaking && commit.type === key,
    );
    if (entries.length === 0) continue;
    sections.push(`### ${title}`, '', ...entries.map(line), '');
  }
}
const compareUrl = `${repoUrl}/compare/${previousTag}...${tag}`;
const notes = bump ? [...sections, `**Full changelog:** ${compareUrl}`, ''] : [];

if (notesFile) writeFileSync(notesFile, notes.join('\n'));
if (process.env.GITHUB_OUTPUT) {
  appendFileSync(process.env.GITHUB_OUTPUT, `tag=${tag}\nversion=${version}\nsha=${sha}\n`);
}

// The changelog gets the same sections under a linked version heading, above
// the previous release. A section that already compares from the previous
// tag is this release written earlier, so it is replaced, even when commits
// that landed since have changed the version.
if (changelogFile && bump) {
  const date = new Date().toISOString().slice(0, 10);
  const entry = `${[`## [${version}](${compareUrl}) (${date})`, '', ...sections].join('\n')}\n`;
  const parts = readFileSync(changelogFile, 'utf8').split(/^(?=## )/m);
  const existing = parts.findIndex(
    (part) => part.startsWith('## [') && part.split('\n')[0].includes(`/compare/${previousTag}...`),
  );
  if (existing !== -1) parts[existing] = entry;
  else parts.splice(parts[0].startsWith('## ') ? 0 : 1, 0, entry);
  writeFileSync(changelogFile, parts.join(''));
}

console.log(JSON.stringify({ previousTag, sha, bump, tag, commits: commits.length }, null, 2));
if (!bump) console.log('No feat, fix, perf, revert or breaking commit since the last release.');
else console.log(`\n${notes.join('\n')}`);
if (changelogFile && bump) console.log(`Added ${version} to ${changelogFile}.`);
