const User = require('../models/User');
const mailer = require('../services/mailer');

/** Lists all users. */
exports.list = async function (req, res) {
  const users = await User.find();
  res.json(users);
};

exports.create = async function (req, res) {
  const user = new User(req.body);
  await user.save();
  mailer.sendWelcome(user.email);
  console.log('created');
  res.status(201).json(user);
};
