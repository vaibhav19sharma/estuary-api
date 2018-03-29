# SPDX-License-Identifier: GPL-3.0+
from __future__ import unicode_literals

from builtins import bytes

from scrapers.base import BaseScraper
from purview.models.bugzilla import BugzillaBug
from purview.models.user import User
from purview.utils.general import timestamp_to_datetime
from purview import log


class BugzillaScraper(BaseScraper):

    def run(self, since=None):
        """
        Main function that runs the Bugzilla scraper
        :param since: a string representing a datetime to start scraping data from
        :return: None
        """
        log.info('Starting initial load of Bugzilla bugs')
        if since is None:
            start_date = self.default_since
        else:
            start_date = timestamp_to_datetime(since)

        bugs = self.get_bugzilla_bugs(start_date)
        log.info('Successfully fetched {0} bugs from teiid'.format(len(bugs)))
        self.update_neo4j(bugs)
        log.info('Initial load of Bugzilla bugs complete!')

    def get_bugzilla_bugs(self, start_date):
        """
        Gets Buzilla bugs info from Teiid
        :param start_date: a datetime object representing a datetime to start scraping data from
        :return: list of dictionaries containing bug info
        """
        log.info('Getting all Bugzilla bugs since {0}'.format(start_date))
        sql_query = """
            SELECT bugs.*, products.name AS product_name, classifications.name AS classification,
                assigned.login_name AS assigned_to_email, reported.login_name AS reported_by_email,
                qa.login_name AS qa_contact_email
            FROM bugzilla.bugs AS bugs
            LEFT JOIN bugzilla.products AS products ON bugs.product_id = products.id
            LEFT JOIN bugzilla.classifications AS classifications
                ON products.classification_id = classifications.id
            LEFT JOIN bugzilla.profiles AS assigned ON bugs.assigned_to = assigned.userid
            LEFT JOIN bugzilla.profiles AS reported ON bugs.reporter = reported.userid
            LEFT JOIN bugzilla.profiles AS qa ON bugs.qa_contact = qa.userid
            WHERE classifications.name = 'Red Hat' AND bugs.creation_ts > '{}'
            """.format(start_date)

        return self.teiid.query(sql=sql_query, retry=3)

    def create_user_node(self, email):
        """
        Creates User node in Neo4j
        :param email: a string representing user's email
        :return: User object
        """
        # If email is a Red Hat email address, username is same as domain name
        # prefix in the email address else store email as username
        if email.split('@')[1] == 'redhat.com':
            username = email.split('@')[0]
        else:
            username = email

        user = User.create_or_update(dict(
            username=username,
            email=email
        ))[0]
        return user

    def update_neo4j(self, bugs):
        """
        Update Neo4j with Bugzilla bugs information from TEIID
        :param bugs: a list of dictionaries
        :return: None
        """
        log.info('Beginning to upload data to Neo4j')
        count = 0

        for bug_dict in bugs:
            bug = BugzillaBug.create_or_update(dict(
                id_=bug_dict['bug_id'],
                severity=bug_dict['bug_severity'],
                status=bug_dict['bug_status'],
                creation_time=bug_dict['creation_ts'],
                modified_time=bug_dict['delta_ts'],
                priority=bug_dict['priority'],
                product_name=bytes(bug_dict['product_name'], 'utf-8').decode(),
                product_version=bug_dict['version'],
                classification=bug_dict['classification'],
                resolution=bug_dict['resolution'],
                target_milestone=bug_dict['target_milestone'],
                votes=bug_dict['votes'],
                short_description=bytes(bug_dict['short_desc'], 'utf-8').decode()
            ))[0]

            count += 1
            log.info('Uploaded {0} bugs out of {1}'.format(count, len(bugs)))

            # Creating User nodes and updating their relationships
            if bug_dict['assigned_to']:
                assignee = self.create_user_node(bug_dict['assigned_to_email'])
                bug.assignees.connect(assignee)
                assignee.bugs_assigned_to.connect(bug)

            if bug_dict['reporter']:
                reporter = self.create_user_node(bug_dict['reported_by_email'])
                bug.reporters.connect(reporter)
                reporter.bugs_reported_by.connect(bug)
                reporter.bugzilla_bugs.connect(bug)

            if bug_dict['qa_contact']:
                qa_contact = self.create_user_node(bug_dict['qa_contact_email'])
                bug.qa_contacts.connect(qa_contact)
                qa_contact.bugs_qa_contact_for.connect(bug)