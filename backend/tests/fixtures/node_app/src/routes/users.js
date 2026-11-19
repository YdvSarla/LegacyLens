const express = require('express');
const controller = require('../controllers/userController');
const { requireAuth } = require('../services/auth');

const router = express.Router();
router.get('/', controller.list);
router.post('/', requireAuth, controller.create);

module.exports = router;
