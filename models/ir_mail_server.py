from email.message import EmailMessage
from email.utils import make_msgid
import datetime
import email
import email.policy
import logging
import re
import smtplib
from socket import gaierror, timeout
from ssl import SSLError
import sys
import threading

import html2text
import idna

from odoo import api, fields, models, tools, _
from odoo.exceptions import UserError
from odoo.tools import ustr, pycompat, formataddr

_logger = logging.getLogger(__name__)
_test_logger = logging.getLogger('odoo.tests')

SMTP_TIMEOUT = 60

class IrMailServer(models.Model):
    _inherit = 'ir.mail_server'

    log_user = fields.Many2one('res.users', string='User')

class SmtpConfiguration(models.Model):
    _name = 'smtp.configuration'
    _rec_name = 'name'

    name = fields.Char('Description', required=True)
    smtp_active = fields.Boolean('Active', default=True)
    smtp_host = fields.Char('SMTP Server', required=True)
    smtp_port = fields.Integer('SMTP Port', required=True)
    smtp_encryption = fields.Selection([('none','None'),('starttls','TLS(STARTTLS)'),('ssl','SSL/TLS')],
                                       string='Connection Security', required=True, default='none')
    smtp_user = fields.Char('Username', required=True)
    smtp_pass = fields.Char('Password', required=True)
    smtp_log_user = fields.Many2one('res.users', string='User', default=lambda self: self.env.user.id,)
    smtp_debug = fields.Boolean(string='Debugging', help="If enabled, the full output of SMTP sessions will "
                                                         "be written to the server log at DEBUG level "
                                                         "(this is very verbose and may include confidential info!)")

    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirm', 'Confirm'),
    ], string='Status', readonly=True, default='draft')

    def connect(self, host=None, port=None, user=None, password=None, encryption=None,
                smtp_debug=False, mail_server_id=None):
        """Returns a new SMTP connection to the given SMTP server.
           When running in test mode, this method does nothing and returns `None`.

           :param host: host or IP of SMTP server to connect to, if mail_server_id not passed
           :param int port: SMTP port to connect to
           :param user: optional username to authenticate with
           :param password: optional password to authenticate with
           :param string encryption: optional, ``'ssl'`` | ``'starttls'``
           :param bool smtp_debug: toggle debugging of SMTP sessions (all i/o
                              will be output in logs)
           :param mail_server_id: ID of specific mail server to use (overrides other parameters)
        """
        # Do not actually connect while running in test mode
        if getattr(threading.currentThread(), 'testing', False):
            return None

        mail_server = smtp_encryption = None
        if mail_server_id:
            mail_server = self.sudo().browse(mail_server_id)
        elif not host:
            mail_server = self.sudo().search([], order='sequence', limit=1)

        if mail_server:
            smtp_server = mail_server.smtp_host
            smtp_port = mail_server.smtp_port
            smtp_user = mail_server.smtp_user
            smtp_password = mail_server.smtp_pass
            smtp_encryption = mail_server.smtp_encryption
            smtp_debug = smtp_debug or mail_server.smtp_debug
        else:
            # we were passed individual smtp parameters or nothing and there is no default server
            smtp_server = host or tools.config.get('smtp_server')
            smtp_port = tools.config.get('smtp_port', 25) if port is None else port
            smtp_user = user or tools.config.get('smtp_user')
            smtp_password = password or tools.config.get('smtp_password')
            smtp_encryption = encryption
            if smtp_encryption is None and tools.config.get('smtp_ssl'):
                smtp_encryption = 'starttls'  # smtp_ssl => STARTTLS as of v7

        if not smtp_server:
            raise UserError(
                (_("Missing SMTP Server") + "\n" +
                 _("Please define at least one SMTP server, "
                   "or provide the SMTP parameters explicitly.")))

        if smtp_encryption == 'ssl':
            if 'SMTP_SSL' not in smtplib.__all__:
                raise UserError(
                    _("Your Odoo Server does not support SMTP-over-SSL. "
                      "You could use STARTTLS instead. "
                      "If SSL is needed, an upgrade to Python 2.6 on the server-side "
                      "should do the trick."))
            connection = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=SMTP_TIMEOUT)
        else:
            connection = smtplib.SMTP(smtp_server, smtp_port, timeout=SMTP_TIMEOUT)
        connection.set_debuglevel(smtp_debug)
        if smtp_encryption == 'starttls':
            # starttls() will perform ehlo() if needed first
            # and will discard the previous list of services
            # after successfully performing STARTTLS command,
            # (as per RFC 3207) so for example any AUTH
            # capability that appears only on encrypted channels
            # will be correctly detected for next step
            connection.starttls()

        if smtp_user:
            # Attempt authentication - will raise if AUTH service not supported
            local, at, domain = smtp_user.rpartition('@')
            domain = idna.encode(domain).decode('ascii')
            connection.login(f"{local}{at}{domain}", smtp_password or '')

        # Some methods of SMTP don't check whether EHLO/HELO was sent.
        # Anyway, as it may have been sent by login(), all subsequent usages should consider this command as sent.
        connection.ehlo_or_helo_if_needed()

        return connection

    def test_smtp_connection(self):
        # mail_server = self.env['ir.mail_server'].sudo()
        # mail_server.test_smtp_connection()
        # a=10
        for server in self:
            smtp = False
            try:
                smtp = self.connect(mail_server_id=server.id)
                # simulate sending an email from current user's address - without sending it!
                email_from, email_to = self.env.user.email, 'noreply@odoo.com'
                if not email_from:
                    raise UserError(_('Please configure an email on the current user to simulate '
                                      'sending an email message via this outgoing server'))
                # Testing the MAIL FROM step should detect sender filter problems
                (code, repl) = smtp.mail(email_from)
                if code != 250:
                    raise UserError(_('The server refused the sender address (%(email_from)s) '
                                      'with error %(repl)s') % locals())
                # Testing the RCPT TO step should detect most relaying problems
                (code, repl) = smtp.rcpt(email_to)
                if code not in (250, 251):
                    raise UserError(_('The server refused the test recipient (%(email_to)s) '
                                      'with error %(repl)s') % locals())
                # Beginning the DATA step should detect some deferred rejections
                # Can't use self.data() as it would actually send the mail!
                smtp.putcmd("data")
                (code, repl) = smtp.getreply()
                if code != 354:
                    raise UserError(_('The server refused the test connection '
                                      'with error %(repl)s') % locals())
            except UserError as e:
                # let UserErrors (messages) bubble up
                raise e
            except (UnicodeError, idna.core.InvalidCodepoint) as e:
                raise UserError(_("Invalid server name !\n %s", ustr(e)))
            except (gaierror, timeout) as e:
                raise UserError(_("No response received. Check server address and port number.\n %s", ustr(e)))
            except smtplib.SMTPServerDisconnected as e:
                raise UserError(_(
                    "The server has closed the connection unexpectedly. Check configuration served on this port number.\n %s",
                    ustr(e.strerror)))
            except smtplib.SMTPResponseException as e:
                raise UserError(_("Server replied with following exception:\n %s", ustr(e.smtp_error)))
            except smtplib.SMTPException as e:
                raise UserError(_("An SMTP exception occurred. Check port number and connection security type.\n %s",
                                  ustr(e.smtp_error)))
            except SSLError as e:
                raise UserError(_("An SSL exception occurred. Check connection security type.\n %s", ustr(e)))
            except Exception as e:
                raise UserError(_("Connection Test Failed! Here is what we got instead:\n %s", ustr(e)))
            finally:
                try:
                    if smtp:
                        smtp.close()
                except Exception:
                    # ignored, just a consequence of the previous exception
                    pass

        title = _("Connection Test Succeeded!")
        message = _("Everything seems properly set up!")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'sticky': False,
            }
        }

    @api.model
    def create(self, vals):
        current_user = self.env.user.id
        smtp_obj = self.env['smtp.configuration'].sudo().search([('create_uid', '=', current_user)], limit=1)
        if smtp_obj:
            raise UserError("You have already created the SMTP configuration.")
        res = super(SmtpConfiguration, self).create(vals)
        return res

    def confirm_smtp(self):
        mail_server = self.env['ir.mail_server'].sudo()
        mail_obj = mail_server.search([('log_user', '=', self.smtp_log_user.id)], limit=1)
        if mail_obj:
            pass
        else:
            mail_server.create({'name': self.name,
                                'active': True,
                                'smtp_host': self.smtp_host,
                                'smtp_port': self.smtp_port,
                                'log_user': self.smtp_log_user.id,
                                'smtp_encryption': self.smtp_encryption,
                                'smtp_user': self.smtp_user,
                                'smtp_pass': self.smtp_pass
                                })
            self.write({'state': 'confirm'})

    def unlink(self):
        mail_server = self.env['ir.mail_server'].sudo()
        mail_obj = mail_server.search([('log_user', '=', self.smtp_log_user.id)], limit=1)
        if mail_obj:
            mail_obj.unlink()
        return super(SmtpConfiguration, self).unlink()


class MailComposerInherit(models.TransientModel):

    _inherit = 'mail.compose.message'

    def get_mail_values(self, res_ids):
        res = super(MailComposerInherit, self).get_mail_values(res_ids)
        mail_server_id_custom = self.env['ir.mail_server'].sudo().search([('log_user', '=', self.env.user.id)], limit=1)
        strings = [str(integer) for integer in res_ids]
        a_string = "".join(strings)
        an_integer = int(a_string)

        if mail_server_id_custom:
            res[an_integer]['mail_server_id'] = mail_server_id_custom.id
            # res.update({'mail_server_id': mail_server_id_custom.id})
        return res