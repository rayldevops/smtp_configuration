# -*- coding: utf-8 -*-
{
    'name': 'Add email configuration as individual user',
    'version': '14.0.1.0.0',
    'category': 'Tools',
    'website': "https://planet-odoo.com",
    'sequence': 1,
    'summary': 'Create and send email from logged  in user',
    'description': """
		Each individual user have option to setup email configuration for sending mail from their email. 
    """,
    'author': "Planet odoo",
    "depends": ['base', 'mail'],
    "data": [
        'security/ir.model.access.csv',
        'views/mail_server_settings.xml',
    ],
    'images': [],
    'installable': True,
    'auto_install': False,
    'application': True,
}
