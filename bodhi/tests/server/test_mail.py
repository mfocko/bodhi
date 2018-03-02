# -*- coding: utf-8 -*-
# Copyright 2017-2018 Red Hat, Inc.
#
# This file is part of Bodhi.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
"""Tests for bodhi.server.mail."""
import smtplib
import unittest

from kitchen.text import converters
import mock

from bodhi.server import config, mail, models
from bodhi.tests.server import base


class TestGetTemplate(base.BaseTestCase):
    """Test the get_template() function."""

    def test_changelog(self):
        """Ensure that a changelog gets generated when there is an older Build."""
        u = self.create_update(['TurboGears-2.0.0.0-1.fc17'])

        t = mail.get_template(u)

        # Assemble the template for easier asserting.
        t = '\n'.join([l for l in t[0]])
        self.assertTrue('ChangeLog:' in t)
        self.assertTrue('* Sat Aug  3 2013 Randy Barlow <bowlofeggs@fp.o> - 2.2.0-1' in t)
        self.assertTrue('- Added some bowlofeggs charm.' in t)
        # Only the new bits of the changelog should be included in the notice, so this should not
        # appear even though it is in the package's changelog.
        self.assertFalse('* Tue Jul 10 2012 Paul Moore <pmoore@redhat.com> - 0.1.0-1' in t)
        self.assertFalse('- Limit package to x86/x86_64 platforms (RHBZ #837888)' in t)

    @mock.patch('bodhi.server.models.RpmBuild.get_latest')
    def test_changelog_no_old_text(self, get_latest):
        """Ensure that a changelog gets generated when there is an older Build with no text."""
        get_latest.return_value = 'TurboGears-1.9.1-1.fc17'
        u = self.create_update(['TurboGears-2.0.0.0-1.fc17'])

        t = mail.get_template(u)

        # Assemble the template for easier asserting.
        t = '\n'.join([l for l in t[0]])
        self.assertTrue('ChangeLog:' in t)
        self.assertTrue('* Sat Aug  3 2013 Randy Barlow <bowlofeggs@fp.o> - 2.2.0-1' in t)
        self.assertTrue('- Added some bowlofeggs charm.' in t)
        # Since we faked the 1.9.1-1 release as having [] as changelogtext in Koji, the entire
        # package changelog should have been included. We'll just spot check it here.
        self.assertTrue('* Tue Jul 10 2012 Paul Moore <pmoore@redhat.com> - 0.1.0-1' in t)
        self.assertTrue('- Limit package to x86/x86_64 platforms (RHBZ #837888)' in t)

    def test_encoding(self):
        """Ensure that the UnicodeDecode is properly handled."""
        u = models.Update.query.first()
        u.alias = '\xe7'

        t = mail.get_template(u)

        # Assemble the template for easier asserting.
        t = '\n'.join([l for l in t[0]])
        self.assertTrue(converters.to_unicode('\xe7') in t)

    def test_module_build(self):
        """ModuleBuilds don't have get_latest(), so lets verify that this is OK."""
        release = self.create_release('27M')
        build = models.ModuleBuild(
            nvr='testmodule:master:1', release=release,
            package=models.Package(name='testmodule', type=models.ContentType.module))
        update = models.Update(builds=[build], release=release)

        # This should not raise an Exception.
        t = mail.get_template(update)

        # Assemble the template for easier asserting.
        t = '\n'.join([l for l in t[0]])
        # No changelog should appear. We can just verify that there's a blank line where the
        # changelog would be.
        self.assertTrue('----\n\nThis update can be installed' in t)

    @mock.patch('bodhi.server.mail.log.debug')
    def test_skip_tracker_bug(self, debug):
        """Tracker security bugs should get skipped."""
        u = models.Update.query.first()
        u.type = models.UpdateType.security
        b = u.bugs[0]
        b.parent = False
        b.title = 'this should not appear'
        u.bugs.append(models.Bug(bug_id=54321, parent=True, title='this should appear'))

        t = mail.get_template(u)

        # Assemble the template for easier asserting.
        t = '\n'.join([l for l in t[0]])
        self.assertTrue('54321 - this should appear' in t)
        self.assertFalse('this should not appear' in t)
        debug.assert_called_once_with(
            ("Skipping tracker bug <Bug {'bug_id': 12345, 'security': False, 'feedback': [], "
             "'parent': False, 'title': 'this should not appear'}>"))

    def test_stable_update(self):
        """Stable updates should not include --enablerepo=updates-testing in the notice."""
        u = models.Update.query.first()
        u.status = models.UpdateStatus.stable

        t = mail.get_template(u)

        # Assemble the template for easier asserting.
        t = '\n'.join([l for l in t[0]])
        self.assertFalse('--enablerepo=updates-testing' in t)
        self.assertFalse('Fedora Test Update Notification' in t)
        # The advisory flag should be included in the dnf instructions.
        self.assertTrue('dnf upgrade --advisory {}'.format(u.alias) in t)

    def test_testing_update(self):
        """Testing updates should include --enablerepo=updates-testing in the notice."""
        u = models.Update.query.first()
        u.status = models.UpdateStatus.testing

        t = mail.get_template(u)

        # Assemble the template for easier asserting.
        t = '\n'.join([l for l in t[0]])
        self.assertTrue('--enablerepo=updates-testing' in t)
        self.assertTrue('Fedora Test Update Notification' in t)
        # The advisory flag should be included in the dnf instructions.
        self.assertTrue(
            'dnf --enablerepo=updates-testing upgrade --advisory {}'.format(u.alias) in t)


class TestSend(base.BaseTestCase):
    """Test the send() function."""

    @mock.patch.dict('bodhi.server.mail.config', {'smtp_server': 'smtp.fp.o'})
    @mock.patch('bodhi.server.mail.smtplib.SMTP')
    def test_msg_type_new(self, SMTP):
        """Assert that the correct header is used for the "new" msg_type."""
        sendmail = SMTP.return_value.sendmail
        update = models.Update.query.all()[0]

        mail.send('bowlofeggs@example.com', 'new', update, agent='bodhi')

        SMTP.assert_called_once_with('smtp.fp.o')
        self.assertEqual(sendmail.call_count, 1)
        sendmail = sendmail.mock_calls[0]
        self.assertEqual(len(sendmail[1]), 3)
        self.assertEqual(sendmail[1][0], 'updates@fedoraproject.org')
        self.assertEqual(sendmail[1][1], ['bowlofeggs@example.com'])
        self.assertTrue('Message-ID: <bodhi-update-{}-{}-{}@{}>'.format(
            update.id, update.user.name, update.release.name,
            config.config.get('message_id_email_domain')) in sendmail[1][2])

    @mock.patch.dict('bodhi.server.mail.config', {'smtp_server': 'smtp.example.com'})
    @mock.patch('bodhi.server.mail.smtplib.SMTP')
    def test_nvr_in_subject(self, SMTP):
        """Assert that the sent e-mail has the full NVR in the subject."""
        update = models.Update.query.all()[0]

        mail.send('fake@news.com', 'comment', update, agent='bowlofeggs')

        SMTP.assert_called_once_with('smtp.example.com')
        sendmail = SMTP.return_value.sendmail
        self.assertEqual(sendmail.call_count, 1)
        self.assertEqual(sendmail.mock_calls[0][1][0], 'updates@fedoraproject.org')
        self.assertEqual(sendmail.mock_calls[0][1][1], ['fake@news.com'])
        self.assertTrue('X-Bodhi-Update-Title: bodhi-2.0-1.fc17' in sendmail.mock_calls[0][1][2])
        self.assertTrue(
            'Subject: [Fedora Update] [comment] bodhi-2.0-1.fc17' in sendmail.mock_calls[0][1][2])

    @mock.patch.dict('bodhi.server.mail.config', {'smtp_server': 'smtp.example.com'})
    @mock.patch('bodhi.server.mail.smtplib.SMTP', side_effect=Exception())
    @mock.patch('bodhi.server.mail.log.exception')
    def test_exception_in__send_mail(self, exception_log, SMTP):
        """Assert that we log an exception if _send_mail catches one"""
        update = models.Update.query.all()[0]

        mail.send('fake@news.com', 'comment', update, agent='bowlofeggs')

        exception_log.assert_called_once_with('Unable to send mail')
        sendmail = SMTP.return_value.sendmail
        self.assertEqual(sendmail.call_count, 0)


class TestSendMail(unittest.TestCase):
    """Test the send_mail() function."""

    @mock.patch.dict('bodhi.server.mail.config',
                     {'exclude_mail': ['fbi@watchingy.ou', 'nsa@spies.biz']})
    @mock.patch('bodhi.server.mail._send_mail')
    def test_exclude_mail(self, _send_mail):
        """Don't send mail if the to_addr is in the exclude_mail setting."""
        mail.send_mail('bowlofeggs@example.com', 'nsa@spies.biz', 'R013X', 'Want a c00l w@tch?')

        # The mail should not have been sent
        self.assertEqual(_send_mail.call_count, 0)

    @mock.patch.dict('bodhi.server.mail.config', {'smtp_server': 'smtp.fp.o'})
    @mock.patch('bodhi.server.mail.smtplib.SMTP')
    def test_headers(self, SMTP):
        """Make sure headers are used correctly."""
        smtp = SMTP.return_value

        mail.send_mail('bodhi@example.com', 'bowlofeggs@example.com', 'R013X', 'Want a c00l w@tch?',
                       headers={'Bodhi-Is': 'Great'})

        SMTP.assert_called_once_with('smtp.fp.o')
        smtp.sendmail.assert_called_once_with(
            'bodhi@example.com', ['bowlofeggs@example.com'],
            ('From: bodhi@example.com\r\nTo: bowlofeggs@example.com\r\nBodhi-Is: Great\r\n'
             'X-Bodhi: fedoraproject.org\r\nSubject: R013X\r\n\r\nWant a c00l w@tch?'))

    @mock.patch.dict('bodhi.server.mail.config', {'bodhi_email': ''})
    @mock.patch('bodhi.server.mail._send_mail')
    @mock.patch('bodhi.server.mail.log.warn')
    def test_no_from_addr(self, warn, _send_mail):
        """If there is no from_addr, a warning should be logged and the function should return."""
        mail.send_mail(None, 'bowlofeggs@example.com', 'R013X', 'Want a c00l w@tch?')

        warn.assert_called_once_with('Unable to send mail: bodhi_email not defined in the config')
        # The mail should not have been sent
        self.assertEqual(_send_mail.call_count, 0)

    @mock.patch.dict('bodhi.server.mail.config', {'smtp_server': 'smtp.fp.o'})
    @mock.patch('bodhi.server.mail.smtplib.SMTP')
    def test_send(self, SMTP):
        """Make sure an e-mail gets sent when conditions are right."""
        smtp = SMTP.return_value

        mail.send_mail('bodhi@example.com', 'bowlofeggs@example.com', 'R013X', 'Want a c00l w@tch?')

        SMTP.assert_called_once_with('smtp.fp.o')
        smtp.sendmail.assert_called_once_with(
            'bodhi@example.com', ['bowlofeggs@example.com'],
            ('From: bodhi@example.com\r\nTo: bowlofeggs@example.com\r\n'
             'X-Bodhi: fedoraproject.org\r\nSubject: R013X\r\n\r\nWant a c00l w@tch?'))


class TestSendReleng(unittest.TestCase):
    """Test the send_releng() function."""

    @mock.patch.dict('bodhi.server.mail.config', {'smtp_server': 'smtp.fp.o'})
    @mock.patch('bodhi.server.mail.smtplib.SMTP')
    def test_send_releng(self, SMTP):
        """Ensure correct operation of the function."""
        smtp = SMTP.return_value

        mail.send_releng('sup', 'r u ready 2 upd8')

        SMTP.assert_called_once_with('smtp.fp.o')
        smtp.sendmail.assert_called_once_with(
            config.config.get('bodhi_email'), [config.config.get('release_team_address')],
            ('From: {}\r\nTo: {}\r\nX-Bodhi: fedoraproject.org\r\nSubject: sup\r\n\r\nr u '
             'ready 2 upd8').format(
                 config.config.get('bodhi_email'), config.config.get('release_team_address')))


class Test_SendMail(unittest.TestCase):
    """Test the _send_mail() function."""

    @mock.patch.dict('bodhi.server.mail.config', {'smtp_server': 'smtp.fp.o'})
    @mock.patch('bodhi.server.mail.log.warn')
    @mock.patch('bodhi.server.mail.smtplib.SMTP')
    def test_recipients_refused(self, SMTP, warn):
        """If recipients are refused, a warning should be logged and SMTP should be exited."""
        smtp = SMTP.return_value
        smtp.sendmail.side_effect = smtplib.SMTPRecipientsRefused('nooope!')

        mail._send_mail('archer@spies.com', 'lana@spies.com', 'hi')

        SMTP.assert_called_once_with('smtp.fp.o')
        smtp.sendmail.assert_called_once_with('archer@spies.com', ['lana@spies.com'], 'hi')
        warn.assert_called_once_with(
            '"recipient refused" for \'lana@spies.com\', SMTPRecipientsRefused(\'nooope!\',)')
        smtp.quit.assert_called_once_with()

    @mock.patch.dict('bodhi.server.mail.config', {'smtp_server': ''})
    @mock.patch('bodhi.server.mail.log.info')
    @mock.patch('bodhi.server.mail.smtplib.SMTP')
    def test_smtp_server_not_configured(self, SMTP, info):
        """If smtp_server is not configured, the function should log and return."""
        mail._send_mail('archer@spies.com', 'lana@spies.com', 'hi')

        self.assertEqual(SMTP.call_count, 0)
        info.assert_called_once_with('Not sending email: No smtp_server defined')
