FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
RUN apk add --no-cache openssl \
    && touch /etc/nginx/auth.inc
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY docker-entrypoint.d/15-dashboard-auth.sh /docker-entrypoint.d/15-dashboard-auth.sh
RUN chmod +x /docker-entrypoint.d/15-dashboard-auth.sh
COPY --from=build /app/dist /usr/share/nginx/html
ENV NGINX_ENVSUBST_FILTER=CRM_API_TOKEN
EXPOSE 80
