import findspark
findspark.init()
from pyspark.sql.window import Window as W
from pyspark.sql.functions import monotonically_increasing_id
from pyspark.sql.functions import udf
from pyspark.sql import Row
from pyspark import SparkConf, SparkContext
from pyspark.sql.functions import lit
from pyspark.sql.types import *
from pyspark.sql.functions import col
from pyspark.sql.functions import when
from pyspark.sql import SQLContext
import pyspark.sql.functions as sf
import datetime
import time
import os
from pyspark.sql import SparkSession
from pyspark.sql.functions import current_timestamp

spark = SparkSession.builder.config('spark.jars.packages', 'com.datastax.spark:spark-cassandra-connector_2.12:3.3.0')\
                            .config("spark.jars.packages","com.mysql:mysql-connector-j:8.0.31")\
                            .getOrCreate()


def calculating_clicks(df):
    clicks_data = df.filter(df.custom_track == 'click')
    clicks_data = clicks_data.na.fill({'bid': 0})
    clicks_data = clicks_data.na.fill({'job_id': 0})
    clicks_data = clicks_data.na.fill({'publisher_id': 0})
    clicks_data = clicks_data.na.fill({'group_id': 0})
    clicks_data = clicks_data.na.fill({'campaign_id': 0})
    clicks_data.registerTempTable('clicks')
    clicks_output = spark.sql("""select job_id , date(ts) as date , hour(ts) as hour , publisher_id , campaign_id , group_id , ROUND(avg(bid),1) as bid_set, count(*) as clicks , sum(bid) as spend_hour from clicks
    group by job_id , date(ts) , hour(ts) , publisher_id , campaign_id , group_id """)
    return clicks_output


def calculating_conversion(df):
    conversion_data = df.filter(df.custom_track == 'conversion')
    conversion_data = conversion_data.na.fill({'job_id': 0})
    conversion_data = conversion_data.na.fill({'publisher_id': 0})
    conversion_data = conversion_data.na.fill({'group_id': 0})
    conversion_data = conversion_data.na.fill({'campaign_id': 0})
    conversion_data.registerTempTable('conversion')
    conversion_output = spark.sql("""select job_id , date(ts) as date , hour(ts) as hour , publisher_id , campaign_id , group_id , count(*) as conversions  from conversion
    group by job_id , date(ts) , hour(ts) , publisher_id , campaign_id , group_id """)
    return conversion_output


def calculating_qualified(df):
    qualified_data = df.filter(df.custom_track == 'qualified')
    qualified_data = qualified_data.na.fill({'job_id': 0})
    qualified_data = qualified_data.na.fill({'publisher_id': 0})
    qualified_data = qualified_data.na.fill({'group_id': 0})
    qualified_data = qualified_data.na.fill({'campaign_id': 0})
    qualified_data.registerTempTable('qualified')
    qualified_output = spark.sql("""select job_id , date(ts) as date , hour(ts) as hour , publisher_id , campaign_id , group_id , count(*) as qualified  from qualified
    group by job_id , date(ts) , hour(ts) , publisher_id , campaign_id , group_id """)
    return qualified_output


def calculating_unqualified(df):
    unqualified_data = df.filter(df.custom_track == 'unqualified')
    unqualified_data = unqualified_data.na.fill({'job_id': 0})
    unqualified_data = unqualified_data.na.fill({'publisher_id': 0})
    unqualified_data = unqualified_data.na.fill({'group_id': 0})
    unqualified_data = unqualified_data.na.fill({'campaign_id': 0})
    unqualified_data.registerTempTable('unqualified')
    unqualified_output = spark.sql("""select job_id , date(ts) as date , hour(ts) as hour , publisher_id , campaign_id , group_id , count(*) as unqualified  from unqualified
    group by job_id , date(ts) , hour(ts) , publisher_id , campaign_id , group_id """)
    return unqualified_output


def process_final_data(clicks_output, conversion_output, qualified_output, unqualified_output):
    final_data = clicks_output.join(conversion_output, ['job_id', 'date', 'hour', 'publisher_id', 'campaign_id', 'group_id'], 'full').\
        join(qualified_output, ['job_id', 'date', 'hour', 'publisher_id', 'campaign_id', 'group_id'], 'full').\
        join(unqualified_output, ['job_id', 'date', 'hour','publisher_id', 'campaign_id', 'group_id'], 'full')
    return final_data


def process_cassandra_data(df):
    clicks_output = calculating_clicks(df)
    conversion_output = calculating_conversion(df)
    qualified_output = calculating_qualified(df)
    unqualified_output = calculating_unqualified(df)
    final_data = process_final_data(clicks_output, conversion_output, qualified_output, unqualified_output)
    return final_data


def retrieve_company_data(url, driver, dbtable_company, user, password):
    company = spark.read.format('jdbc').options(url=url, driver=driver, dbtable=dbtable_company, user=user, password=password).load()
    company = company.select("id", "company_id", "group_id","campaign_id").withColumnRenamed("id", "job_id")
    return company


def import_to_mysql(output,url,driver,dbtable,user,password):
    final_output = output.select('job_id', 'date', 'hour', 'publisher_id', 'company_id', 'campaign_id','group_id', 
                                 'unqualified', 'qualified', 'conversions', 'clicks', 'bid_set', 'spend_hour')
    final_output = final_output.withColumnRenamed('date', 'dates')\
                               .withColumnRenamed('hour', 'hours')\
                               .withColumnRenamed('qualified', 'qualified_application')\
                               .withColumnRenamed('unqualified', 'disqualified_application')\
                               .withColumnRenamed('conversions', 'conversion')
    final_output = final_output.withColumn('sources', lit('Cassandra'))
    final_output = final_output.withColumn("latest_update_time", current_timestamp())
    final_output.printSchema()
    final_output.write.format("jdbc") \
                      .option("url", url) \
                      .option("driver", driver) \
                      .option("dbtable", dbtable) \
                      .option("user", user) \
                      .option("password", password) \
                      .mode("append") \
                      .save()
    return print('Data imported successfully')


def main_task(mysql_time, host, port, db_name, url, driver, dbtable, user, password):
    print('The host is ', host)
    print('The port using is ', port)
    print('The db using is ', db_name)
    print('-----------------------------')
    print('Retrieving data from Cassandra')
    print('-----------------------------')
    df = spark.read.format("org.apache.spark.sql.cassandra")\
                   .options(table="tracking", keyspace="study_de")\
                   .load()\
                   .where(col('ts') >= mysql_time)
    print('-----------------------------')
    print('Selecting data from Cassandra')
    print('-----------------------------')
    df = df.select('ts', 'job_id', 'custom_track', 'bid','campaign_id', 'group_id', 'publisher_id')
    df = df.filter(df.job_id.isNotNull())
    df.printSchema()
    print('-----------------------------')
    print('Processing Cassandra Output')
    print('-----------------------------')
    cassandra_output = process_cassandra_data(df)
    print('-----------------------------')
    print('Merge Company Data')
    print('-----------------------------')
    dbtable_company = "job"
    company = retrieve_company_data(url, driver, dbtable_company, user, password)
    print('-----------------------------')
    print('Finalizing Output')
    print('-----------------------------')
    final_output = cassandra_output.join(company, 'job_id', 'left').drop(company.group_id).drop(company.campaign_id)
    print('-----------------------------')
    print('Import Output to MySQL')
    print('-----------------------------')
    import_to_mysql(final_output, url, driver, dbtable, user, password)
    return print('Task Finished')


def get_latest_time_cassandra():
    data = spark.read.format("org.apache.spark.sql.cassandra").options(table='tracking', keyspace='study_de').load()
    cassandra_latest_time = data.agg({'ts': 'max'}).take(1)[0][0]
    return cassandra_latest_time


def get_mysql_latest_time(url, driver,dbtable, user, password):
    mysql_time = spark.read.format('jdbc')\
                           .option('url',url)\
                           .option('driver',driver)\
                           .option('dbtable',dbtable)\
                           .option('user',user)\
                           .option('password',password)\
                           .load()
    mysql_time = mysql_time.agg({'latest_update_time': 'max'}).take(1)[0][0]
    if mysql_time is None:
        mysql_latest = '1998-01-01 23:59:59'
    else:
        mysql_latest = mysql_time.strftime('%Y-%m-%d %H:%M:%S')
    return mysql_latest


host = 'localhost'
port = '3306'
db_name = 'data_engineer'
user = 'root'
password = 'mysql'
url = 'jdbc:mysql://' + host + ':' + port + '/' + db_name
driver = "com.mysql.cj.jdbc.Driver"
dbtable = "result"
while True:
    start_time = datetime.datetime.now()
    cassandra_time = get_latest_time_cassandra()
    print('Cassandra latest time is {}'.format(cassandra_time))
    mysql_time = get_mysql_latest_time(url, driver, dbtable, user, password)
    print('MySQL latest time is'.format(mysql_time))
    if cassandra_time > mysql_time:
        main_task(mysql_time, host, port, db_name, url, driver, dbtable, user, password)
    else:
        print("No new data found")
    end_time = datetime.datetime.now()
    execution_time = (end_time - start_time).total_seconds()
    print('Job takes {} seconds to execute'.format(execution_time))
    time.sleep(30)
