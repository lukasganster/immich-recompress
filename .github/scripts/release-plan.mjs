#!/usr/bin/env node
// Plans the next release from the Conventional Commits since the last
// stable tag. A breaking change bumps major, feat bumps minor, and fix,
// perf or revert bump patch. Other types do not produce a release.
//
// Usage: node .github/scripts/release-plan.mjs [--to <ref>] [--notes <file>]
// Prints the plan as JSON and, inside GitHub Actions, writes the tag,
// version and sha outputs. The tag output is empty when nothing is due.

import { execFileSync } from 'node:child_process';
import { appendFileSync, writeFileSync } from 'node:fs';

const args = process.argv.slice(2);
const option = (name) => {
  const index = args.indexOf(name);
  return index === -1 ? undefined : args[index + 1];
};
const to = option('--to') ?? 'HEAD';
const notesFile = option('--notes');

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

const notes = [];
if (bump) {
  for (const [key, title] of SECTIONS) {
    const entries = releasable.filter((commit) =>
      key === 'breaking' ? commit.breaking : !commit.breaking && commit.type === key,
    );
    if (entries.length === 0) continue;
    notes.push(`### ${title}`, '', ...entries.map(line), '');
  }
  notes.push(`**Full changelog:** ${repoUrl}/compare/${previousTag}...${tag}`, '');
}

if (notesFile) writeFileSync(notesFile, notes.join('\n'));
if (process.env.GITHUB_OUTPUT) {
  appendFileSync(process.env.GITHUB_OUTPUT, `tag=${tag}\nversion=${version}\nsha=${sha}\n`);
}

console.log(JSON.stringify({ previousTag, sha, bump, tag, commits: commits.length }, null, 2));
if (!bump) console.log('No feat, fix, perf, revert or breaking commit since the last release.');
else console.log(`\n${notes.join('\n')}`);
