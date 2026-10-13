"""Generate aggregate cards without publishing repository names or private metadata."""
import json
import math
import os
import subprocess
from collections import defaultdict
from html import escape
from pathlib import Path

QUERY = '''query($login: String!, $after: String) {
  user(login: $login) {
    contributionsCollection {
      startedAt endedAt restrictedContributionsCount
      totalCommitContributions totalIssueContributions
      totalPullRequestContributions totalPullRequestReviewContributions
      contributionCalendar { totalContributions }
    }
    repositoriesContributedTo(first: 100, after: $after,
      includeUserRepositories: true,
      contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, PULL_REQUEST_REVIEW]) {
      nodes { id isFork owner { __typename }
        languages(first: 100) {
          totalCount edges { size node { name color } }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}'''


def graphql(login, after=None, query=QUERY):
    payload = {'query': query, 'variables': {'login': login, 'after': after}}
    result = subprocess.run(
        ['gh', 'api', 'graphql', '--input', '-'], input=json.dumps(payload),
        text=True, capture_output=True, check=True,
    )
    response = json.loads(result.stdout)
    if response.get('errors') or not response.get('data', {}).get('user'):
        raise RuntimeError('GitHub did not return complete profile data')
    return response['data']['user']


def collect(login, request=graphql):
    repositories = {}
    after = None
    collection = None
    seen_cursors = set()
    while True:
        user = request(login, after)
        if collection is None:
            collection = user['contributionsCollection']
        page = user['repositoriesContributedTo']
        for repo in page['nodes']:
            if repo is None:
                raise RuntimeError('GitHub returned an inaccessible repository')
            repositories[repo['id']] = repo
        if not page['pageInfo']['hasNextPage']:
            break
        after = page['pageInfo']['endCursor']
        if not after or after in seen_cursors:
            raise RuntimeError('Repository pagination did not advance')
        seen_cursors.add(after)
    return collection, list(repositories.values())


def language_totals(repositories):
    totals = defaultdict(int)
    colors = {}
    for repo in repositories:
        languages = repo['languages']
        if languages['totalCount'] > len(languages['edges']):
            raise RuntimeError('Repository language data was truncated')
        for edge in languages['edges']:
            name = edge['node']['name']
            totals[name] += edge['size']
            colors[name] = edge['node']['color'] or '#cdd6f4'
    return totals, colors


def text(x, y, value, size=14, color='#cdd6f4', weight=400):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{escape(color)}" '
            f'font-weight="{weight}">{escape(str(value))}</text>')


def card(title, description, body, height=320):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="467" height="{height}" '
            f'viewBox="0 0 467 {height}" role="img" aria-labelledby="title desc">'
            f'<title id="title">{escape(title)}</title><desc id="desc">{escape(description)}</desc>'
            '<style>text{font-family:Segoe UI,Ubuntu,Arial,sans-serif;font-variant-numeric:tabular-nums}</style>'
            f'<rect x=".5" y=".5" width="466" height="{height-1}" rx="4.5" '
            'fill="#1e1e2e" stroke="#e4e2e2"/>'
            + text(25, 35, title, 18, '#94e2d5', 600) + body + '</svg>\n')


def donut(parts, cx, cy, radius, thickness=17):
    """Render real proportions, including small slices and a complete single slice."""
    total = sum(value for value, _ in parts)
    result = f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="#313244" stroke-width="{thickness}"/>'
    if not total:
        return result
    circumference = 2 * math.pi * radius
    offset = 0
    for value, color in parts:
        if value <= 0:
            continue
        length = circumference * value / total
        result += (f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" '
                   f'stroke="{escape(color)}" stroke-width="{thickness}" '
                   f'stroke-dasharray="{length:.6f} {circumference:.6f}" '
                   f'stroke-dashoffset="{-offset:.6f}" transform="rotate(-90 {cx} {cy})"/>')
        offset += length
    return result


# Adapted from readme-tools/github-readme-stats/src/calculateRank.js.
# Rank uses annual commits and PRs, owned-repository stars and followers.
# Issues and reviews are excluded; the remaining weights sum to 10.
def calculate_rank(collection, stars, followers):
    exp = lambda value: 1 - 2 ** (-value)
    log = lambda value: value / (1 + value)
    weighted = (
        2 * exp(collection['totalCommitContributions'] / 250)
        + 3 * exp(collection['totalPullRequestContributions'] / 50)
        + 4 * log(stars / 50)
        + log(followers / 10)
    )
    percentile = max(0, min(100, 100 * (1 - weighted / 10)))
    levels = [(1, 'S'), (12.5, 'A+'), (25, 'A'), (37.5, 'A-'),
              (50, 'B+'), (62.5, 'B'), (75, 'B-'), (87.5, 'C+'), (100, 'C')]
    return next(level for threshold, level in levels if percentile <= threshold), percentile


def rank_profile(login):
    query = '''query($login: String!, $after: String) {
      user(login: $login) {
        followers { totalCount }
        repositories(first: 100, after: $after, ownerAffiliations: OWNER) {
          nodes { stargazerCount }
          pageInfo { hasNextPage endCursor }
        }
      }
    }'''
    stars = 0
    after = None
    seen = set()
    while True:
        user = graphql(login, after, query)
        page = user['repositories']
        stars += sum(repo['stargazerCount'] for repo in page['nodes'])
        if not page['pageInfo']['hasNextPage']:
            return stars, user['followers']['totalCount']
        after = page['pageInfo']['endCursor']
        if not after or after in seen:
            raise RuntimeError('Star pagination did not advance')
        seen.add(after)


def render(collection, repositories, stars=0, followers=0):
    total = collection['contributionCalendar']['totalContributions']
    restricted = collection['restrictedContributionsCount']
    start = collection['startedAt'][:10]
    end = collection['endedAt'][:10]
    commits = collection['totalCommitContributions']
    prs = collection['totalPullRequestContributions']
    issues = collection['totalIssueContributions']
    reviews = collection['totalPullRequestReviewContributions']
    body = text(25, 59, 'Last 12 months · public & private', 12, '#a6adc8')
    rows = [('Total contributions', total), ('Commits', commits),
            ('Pull requests', prs), ('Code reviews', reviews)]
    if restricted:
        rows.append(('Private activity', restricted))
    for i, (label, count) in enumerate(rows):
        y = 98 + i * 27
        body += f'<circle cx="32" cy="{y-5}" r="4" fill="#cba6f7"/>'
        body += text(48, y, label, 14, weight=600)
        body += f'<text x="280" y="{y}" text-anchor="end" font-size="14" fill="#cdd6f4" font-weight="600">{count:,}</text>'
    level, percentile = calculate_rank(collection, stars, followers)
    body += donut([(100 - percentile, '#94e2d5'), (percentile, '#313244')], 380, 158, 51, 9)
    body += f'<text x="380" y="168" text-anchor="middle" font-size="30" fill="#cdd6f4" font-weight="700">{level}</text>'
    body += text(25, 299, f'{start} to {end} · updated daily', 11, '#a6adc8')
    stats = card("Alp Sarıyer's GitHub Stats",
                 f'{total} contributions from {start} to {end}. Includes private activity. '
                 f'Activity rank: {level}.', body)

    totals, colors = language_totals(repositories)
    denominator = sum(totals.values())
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    shown = ranked[:6]
    if len(ranked) > 6:
        shown.append(('Other', sum(size for _, size in ranked[6:])))
    org_count = sum(repo['owner']['__typename'] == 'Organization' for repo in repositories)
    body = text(25, 59, 'Personal & organization repositories · last 12 months', 12, '#a6adc8')
    body += donut([(size, colors.get(name, '#a6adc8')) for name, size in shown], 233.5, 132, 53)
    for i, (name, size) in enumerate(shown):
        x = 32 + (i % 2) * 223
        y = 211 + (i // 2) * 23
        body += f'<circle cx="{x}" cy="{y-5}" r="6" fill="{escape(colors.get(name, "#a6adc8"))}"/>'
        body += text(x + 13, y, f'{name} {size / denominator * 100:.1f}%', 14)
    if not denominator:
        body += text(25, 211, 'No language data available.')
    body += text(25, 310, f'{len(repositories)} repositories · {org_count} organization repositories', 11, '#a6adc8')
    langs = card('Most Used Languages',
                 'Language distribution by code bytes in accessible personal and organization repositories. '
                 'Not a measure of personally authored code.', body)
    return stats, langs, {'contributions': total, 'repositories': len(repositories), 'organization_repositories': org_count,
                          'restricted_contributions': restricted}


def main():
    login = os.environ.get('PROFILE_USERNAME', 'ravenwits')
    collection, repositories = collect(login)
    stars, followers = rank_profile(login)
    stats, languages, summary = render(collection, repositories, stars, followers)
    output = Path('profile')
    output.mkdir(exist_ok=True)
    (output / 'stats.svg').write_text(stats)
    (output / 'top-langs.svg').write_text(languages)
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
