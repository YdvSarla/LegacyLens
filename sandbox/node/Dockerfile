# Node 24 has the built-in test runner. tsx runs TypeScript the way real projects write it,
# with extensionless imports and decorators, which Node's own type stripping does not support.
FROM node:24-slim
RUN npm install --global --no-audit --no-fund tsx@4
RUN install -d -o 1000 -g 1000 /workspace
USER 1000:1000
WORKDIR /workspace
