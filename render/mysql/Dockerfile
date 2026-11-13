# The dashboard's MySQL database on Render (a private service: only our own Render services can reach it).
# Official MySQL 8.4 (the long-term-support release). Its data lives on the Render disk mounted at /var/lib/mysql.
FROM mysql:8.4
COPY aymetrix.cnf /etc/mysql/conf.d/aymetrix.cnf
