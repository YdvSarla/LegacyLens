function sendWelcome(address) {
  return `welcome ${address}`;
}

function formatSubject(name) {
  return name ? `Hello ${name}` : 'Hello';
}

module.exports = { sendWelcome, formatSubject };
