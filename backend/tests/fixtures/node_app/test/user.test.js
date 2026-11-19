const { sendWelcome } = require('../src/services/mailer');
test('welcome', () => expect(sendWelcome('a')).toBe('welcome a'));
