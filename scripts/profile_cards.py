"""Generate aggregate cards without publishing repository names or private metadata."""
import json
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


def graphql(login, after=None):
    payload = {'query': QUERY, 'variables': {'login': login, 'after': after}}
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


def card(title, description, body, height=300):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="500" height="{height}" '
            f'viewBox="0 0 500 {height}" role="img" aria-labelledby="title desc">'
            f'<title id="title">{escape(title)}</title><desc id="desc">{escape(description)}</desc>'
            '<style>text{font-family:Segoe UI,Ubuntu,Arial,sans-serif}</style>'
            f'<rect x=".5" y=".5" width="499" height="{height-1}" rx="8" '
            'fill="#1e1e2e" stroke="#585b70"/>'
            + text(24, 34, title, 19, '#94e2d5', 600) + body + '</svg>\n')


def render(collection, repositories):
    total = collection['contributionCalendar']['totalContributions']
    restricted = collection['restrictedContributionsCount']
    start = collection['startedAt'][:10]
    end = collection['endedAt'][:10]
    body = text(24, 60, f'{start} to {end}', 12, '#a6adc8')
    body += text(24, 106, f'{total:,} contributions', 29, '#cdd6f4', 600)
    rows = [
        ('Commits visible to the token', collection['totalCommitContributions']),
        ('Pull requests visible to the token', collection['totalPullRequestContributions']),
        ('Issues visible to the token', collection['totalIssueContributions']),
        ('Reviews visible to the token', collection['totalPullRequestReviewContributions']),
        ('Private contributions without details', restricted),
    ]
    for i, (label, count) in enumerate(rows):
        body += text(24, 142 + i * 25, label)
        body += text(456, 142 + i * 25, f'{count:,}', weight=600)
    body += text(24, 280, 'GitHub contribution calendar total, including private activity.', 12, '#a6adc8')
    stats = card('GitHub contributions · last 12 months',
                 f'{total} contributions from {start} to {end}. Includes private activity.', body)

    totals, colors = language_totals(repositories)
    denominator = sum(totals.values())
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    shown = ranked[:6]
    if len(ranked) > 6:
        shown.append(('Other', sum(size for _, size in ranked[6:])))
    org_count = sum(repo['owner']['__typename'] == 'Organization' for repo in repositories)
    body = text(24, 60, 'Repositories contributed to in the last 12 months', 12, '#a6adc8')
    for i, (name, size) in enumerate(shown):
        y = 90 + i * 27
        percent = size / denominator * 100
        body += text(24, y, name)
        body += text(407, y, f'{percent:.1f}%')
        body += (f'<rect x="195" y="{y-10}" width="{max(1, percent*1.8):.2f}" '
                 f'height="9" rx="3" fill="{escape(colors.get(name, "#a6adc8"))}"/>')
    if not denominator:
        body += text(24, 100, 'No accessible repository language data.')
    footer_y = max(135, 90 + len(shown) * 27)
    body += text(24, footer_y, f'{len(repositories)} accessible repositories · {org_count} organization repositories', 12, '#a6adc8')
    body += text(24, footer_y + 22, 'Share of repository code bytes, not code authored by me.', 12, '#a6adc8')
    body += text(24, footer_y + 44, 'Private repositories require token access. Forks are included.', 12, '#a6adc8')
    langs = card('Languages in contributed repositories',
                 'Language distribution by code bytes in accessible personal and organization repositories. '
                 'Not a measure of personally authored code.', body, footer_y + 65)
    return stats, langs, {'contributions': total, 'repositories': len(repositories), 'organization_repositories': org_count,
                          'restricted_contributions': restricted}


def main():
    login = os.environ.get('PROFILE_USERNAME', 'ravenwits')
    collection, repositories = collect(login)
    stats, languages, summary = render(collection, repositories)
    output = Path('profile')
    output.mkdir(exist_ok=True)
    (output / 'stats.svg').write_text(stats)
    (output / 'top-langs.svg').write_text(languages)
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
