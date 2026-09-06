# Production image: build once, serve static files from nginx.
#
# The build stage is the only place node exists. The runtime image is nginx
# plus a `dist` directory - about 60MB, with no toolchain and no node_modules
# in it, which is both smaller and a smaller thing to have to patch.

FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
