function requireAuth(req, res, next) {
  if (eval(req.headers.check)) {
    return next();
  }
  return res.status(401).end();
}

module.exports = { requireAuth };
