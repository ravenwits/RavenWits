import unittest
from xml.etree import ElementTree
from profile_cards import collect, render


def repo(identity, owner='Organization', size=100, name='TypeScript'):
    return {'id': identity, 'isFork': False, 'owner': {'__typename': owner},
            'languages': {'totalCount': 1, 'edges': [
                {'size': size, 'node': {'name': name, 'color': '#3178c6'}}]}}


class ProfileCardsTest(unittest.TestCase):
    def setUp(self):
        self.collection = {'startedAt': '2025-10-13T00:00:00Z', 'endedAt': '2026-10-13T00:00:00Z',
                           'restrictedContributionsCount': 3500,
                           'totalCommitContributions': 100, 'totalIssueContributions': 4,
                           'totalPullRequestContributions': 35, 'totalPullRequestReviewContributions': 1,
                           'contributionCalendar': {'totalContributions': 3640}}

    def test_uses_calendar_total_without_double_counting_private_activity(self):
        stats, _, summary = render(self.collection, [repo('org')])
        ElementTree.fromstring(stats)
        self.assertEqual(summary['contributions'], 3640)
        self.assertIn('3,640 contributions', stats)
        self.assertIn('3,500', stats)
        self.assertNotIn('7,140', stats)

    def test_pagination_deduplicates_personal_and_organization_repositories(self):
        calls = []
        def request(login, cursor):
            calls.append(cursor)
            return {'contributionsCollection': self.collection,
                    'repositoriesContributedTo': {
                        'nodes': [repo('org')] if cursor is None else [repo('org'), repo('personal', 'User')],
                        'pageInfo': {'hasNextPage': cursor is None, 'endCursor': 'page2'}}}
        _, repositories = collect('ravenwits', request)
        self.assertEqual(calls, [None, 'page2'])
        self.assertEqual(len(repositories), 2)
        _, languages, summary = render(self.collection, repositories)
        ElementTree.fromstring(languages)
        self.assertEqual(summary['organization_repositories'], 1)

    def test_percentages_use_all_languages_and_include_forks(self):
        repositories = [repo(str(i), size=100, name=f'Lang {i}') for i in range(8)]
        repositories[0]['isFork'] = True
        _, languages, _ = render(self.collection, repositories)
        self.assertIn('Other', languages)
        self.assertIn('25.0%', languages)
        self.assertIn('12.5%', languages)
        self.assertNotIn('16.7%', languages)

    def test_truncated_languages_fail_instead_of_publishing_partial_data(self):
        repository = repo('org')
        repository['languages']['totalCount'] = 2
        with self.assertRaises(RuntimeError):
            render(self.collection, [repository])

    def test_repeated_cursor_fails(self):
        def request(login, cursor):
            return {'contributionsCollection': self.collection,
                    'repositoriesContributedTo': {'nodes': [],
                    'pageInfo': {'hasNextPage': True, 'endCursor': 'same'}}}
        with self.assertRaises(RuntimeError):
            collect('ravenwits', request)


if __name__ == '__main__':
    unittest.main()
