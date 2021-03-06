# Copyright wattTime 2013
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Authors: Anna Schneider

# regular imports
from django.core.mail import send_mail
from windfriendly.models import MARGINAL_FUELS, CAISO, NE
from windfriendly.balancing_authorities import BALANCING_AUTHORITIES, BA_MODELS, BA_PARSERS
import accounts.models
from accounts.regions import california, newengland
from accounts.twilio_utils import send_text
from accounts.models import UserProfile
from accounts.messages import morning_forecast_email, morning_forecast_email_first, ne_message_dirty_daytime, ne_message_dirty_evening, ne_message_clean
from workers.utils import debug, send_daily_report, perform_scheduled_tasks, schedule_task, send_ca_texts, add_to_report
from workers.models import latest_by_category, LastMessageSent
import datetime
import pytz
import twitter

# XXX
# Why does this not work? !!!!
# import settings

from settings import EMAIL_HOST_USER, TWITTER_CA_CONSUMER_KEY, TWITTER_CA_CONSUMER_SECRET, TWITTER_CA_ACCESS_KEY, TWITTER_CA_ACCESS_SECRET

def run_frequent_tasks():
    """ Should be run every 5-10 min by a clock process or scheduler """
    # scrape new info from utilities
    updated_bas = update_bas(['BPA', 'ISONE', 'MISO'])
    perform_scheduled_tasks()
    send_ne_texts_if_necessary()
    print updated_bas

    # send notifications to users in updated regions
   # notified_users = send_text_notifications(['ISONE'])
   # print notified_users

def run_hourly_tasks():
    """ Should be run every hour by a clock process or scheduler """
    # scrape new info from utilities
    updated_bas = update_bas(['CAISO'])
    print updated_bas

    # send notifications to users in updated regions
   # notified_users = send_text_notifications(['CAISO'])
   # print notified_users

utc = pytz.utc
def _now(tz = None):
    now = datetime.datetime.now(utc)
    if tz is None:
        return now
    else:
        return convert_tz(now, tz)

def convert_tz(dt, tz):
    if isinstance(tz, str):
        tz = pytz.timezone(tz)
    return tz.normalize(dt.astimezone(tz))

def this_hour(tz = None):
    now = _now(tz)
    dt_ = now.replace(minute = 0, second = 0, microsecond = 0)
    if now.minute < 30:
        return dt_
    else:
        return dt_ + datetime.timedelta(hours = 1)

def display_hour(dt, tz = None):
    if tz is not None:
        dt = convert_tz(dt, tz)

    hour = dt.hour
    if dt.minute >= 30:
        hour = (hour + 1) % 24

    if hour == 0:
        return 'midnight'
    elif hour < 12:
        return '{:d}am'.format(hour)
    elif hour == 12:
        return 'noon'
    else:
        return '{:d}pm'.format(hour - 12)

# Run at 14.00 UTC every day
def run_daily_tasks_1400():
    send_ca_forecast_emails()
    prepare_to_send_ca_texts()
    send_daily_report()

def send_ca_forecast_emails():
    # Send morning forecasts to california users.
    tz = 'America/Los_Angeles'
    now_ca = this_hour(tz)
    start = convert_tz(now_ca.replace(hour = 8), utc) # 8am PST
    end = start + datetime.timedelta(hours = 14) # 10pm PST

    rows = CAISO.objects.all().filter(date__range=(start, end)).best_guess_points()
    best_time = max(rows, key = (lambda r : r.fraction_clean)).date
    worst_time = min(rows, key = (lambda r : r.fraction_clean)).date

    best_hour = display_hour(best_time, tz)
    worst_hour = display_hour(worst_time, tz)

    subj = 'WattTime {} forecast: {} cleanest, {} dirtiest'
    subj = subj.format(start.strftime('%m/%d'), best_hour, worst_hour)
    # subj = 'WattTime forecast #1'

    for up in UserProfile.objects.all():
        if up.user.is_active and up.state == 'CA':
            ca = up.get_region_settings()
            if ca.forecast_email:
                # Send email to that user.
                msg = morning_forecast_email(up.name, best_hour, worst_hour)
                # msg = morning_forecast_email_first(up.name, best_hour, worst_hour)
                send_mail(subj, msg, EMAIL_HOST_USER, [up.email])

def prepare_to_send_ca_texts():
    now_ca = this_hour('America/Los_Angeles')

    # Dirty texts
    dirty_start = convert_tz(now_ca.replace(hour = 8), utc) # 8am PST
    dirty_end = dirty_start + datetime.timedelta(hours = 14) # 10pm PST

    rows = CAISO.objects.all().filter(date__range=(dirty_start, dirty_end)).best_guess_points()
    worst_time = min(rows, key = (lambda r : r.fraction_clean)).date

    schedule_task(worst_time, "workers.utils.send_ca_texts(0)")

    add_to_report('Scheduled "dirty" texts to go out at {}'.format(worst_time))

    # Clean texts
    clean_start = convert_tz(now_ca.replace(hour = 17), utc) # 5pm PST
    clean_end = clean_start + datetime.timedelta(hours = 5) # 10pm PST

    rows = CAISO.objects.all().filter(date__range=(clean_start, clean_end)).best_guess_points()
    best_time = max(rows, key = (lambda r : r.fraction_clean)).date

    schedule_task(best_time, "workers.utils.send_ca_texts(1)")

    add_to_report('Scheduled "clean" texts to go out at {}'.format(best_time))

def send_ne_texts_if_necessary():
    now = _now()
    now_ne = _now('America/New_York')

    is_weekday = (now_ne.isoweekday() <= 5)
    is_daytime = (9 <= now_ne.hour < 17)
    is_evening = (17 <= now_ne.hour < 22)

    fuel = NE.objects.all().latest().marginal_fuel
    fuel_name = MARGINAL_FUELS[fuel]
    # 0 = coal
    # 1 = oil
    # 2 = natural gas
    # 3 = refuse
    # 4 = hydro
    # 5 = wood
    # 6 = nuclear
    # 7 = solar
    # 8 = wind
    # 9 = none
    dirty_fuel = [0, 1]
    clean_fuel = [8, 7, 6, 5, 4]

    is_dirty = fuel in dirty_fuel
    is_clean = fuel in clean_fuel

    # No more than one message in each 20 hour period
    last_okay_time = now - datetime.timedelta(hours = 20)

    ups = UserProfile.objects.all()

    # Dirty texts, working hours
    if is_weekday and is_daytime and is_dirty:
        last_msg = latest_by_category(LastMessageSent.NE_dirty_daytime)
        if last_msg is None or last_msg.date < last_okay_time:
            if last_msg is None:
                last_msg = LastMessageSent()
                last_msg.category = LastMessageSent.NE_dirty_daytime
            last_msg.date = now
            last_msg.save()

            debug("Sending NE texts for dirty, working hours {}".format(last_msg.date))

            for up in ups:
                if up.user.is_active and up.is_verified and up.region() == newengland:
                    if up.get_region_settings().message_frequency == 0:
                        message = ne_message_dirty_daytime(up, fuel_name)
                        res = send_text(message, up)

                        msg = 'Sent text "{}" to {}'.format(message.msg, up)
                        if not res:
                            msg = "FAILED: " + msg
                        debug(msg)
                        add_to_report(msg)

    # Dirty texts, after hours
    if is_dirty and (is_evening or (is_daytime and (not is_weekday))):
        last_msg = latest_by_category(LastMessageSent.NE_dirty_evening)
        if last_msg is None or last_msg.date < last_okay_time:
            if last_msg is None:
                last_msg = LastMessageSent()
                last_msg.category = LastMessageSent.NE_dirty_evening
            last_msg.date = now
            last_msg.save()

            debug("Sending NE texts for dirty, after hours {}".format(last_msg.date))

            for up in ups:
                if up.user.is_active and up.is_verified and up.region() == newengland:
                    if up.get_region_settings().message_frequency == 1:
                        message = ne_message_dirty_evening(up, fuel_name)
                        res = send_text(message, up)

                        msg = 'Sent text "{}" to {}'.format(message.msg, up)
                        if not res:
                            msg = "FAILED: " + msg
                        debug(msg)
                        add_to_report(msg)

    # Clean texts, after hours
    if is_clean and (is_evening or (is_daytime and (not is_weekday))):
        last_msg = latest_by_category(LastMessageSent.NE_clean)
        if last_msg is None or last_msg.date < last_okay_time:
            if last_msg is None:
                last_msg = LastMessageSent()
                last_msg.category = LastMessageSent.NE_clean
            last_msg.date = now
            last_msg.save()

            debug("Sending NE texts for clean {}".format(last_msg.date))

            for up in ups:
                if up.user.is_active and up.is_verified and up.region() == newengland:
                    if up.get_region_settings().message_frequency == 2:
                        message = ne_message_clean(up, fuel_name)
                        res = send_text(message, up)

                        msg = 'Sent text "{}" to {}'.format(message.msg, up)
                        if not res:
                            msg = "FAILED: " + msg
                        debug(msg)
                        add_to_report(msg)
                        
def send_tweet(ba_name):
    # set up dates for average
    utc_end = datetime.datetime.utcnow().replace(tzinfo=pytz.UTC)
    utc_start = utc_end - datetime.timedelta(weeks=2)
    rows = BA_MODELS[ba_name].objects.all().filter(date__range=(utc_start, utc_end))
    
    # get data
    try:
        actual_point = rows.filter(forecast_code=0).latest()
        forecast_point = rows.filter(forecast_code=1, date=actual_point.date).latest()
        rows_to_average = rows.filter(forecast_code=0).filter(date__hour=utc_end.hour)
        previous_point = rows.filter(forecast_code=0).order_by('-date')[1]
    except:
        actual_point = rows.latest()
        forecast_point = None
        rows_to_average = rows.filter(date__hour=utc_end.hour)
        previous_point = rows.order_by('-date')[1]
    
    # get values
    actual_percent = round(max(0.0, actual_point.fraction_clean) * 100.0, 1)
    previous_percent = round(max(0.0, previous_point.fraction_clean) * 100.0, 1)
    average_percent = round(max(0.0, sum([r.fraction_clean for r in rows_to_average]) / float(rows_to_average.count())) * 100.0, 1)
    if forecast_point:
        forecast_percent = round(max(0.0, forecast_point.fraction_clean) * 100.0, 1)
    else:
        forecast_percent = None

    # assemble tweet
    tweet_text = "Your energy is %0.1f%s clean right now. " % (actual_percent, '%')
    tweet_text += "Monitor the grid at http://WattTime.com/status/ "
    if actual_percent == average_percent:
        tweet_text += "#SameAsAverage "
    elif actual_percent > average_percent:
        tweet_text += "#CleanerThanAverage "
    else:
        tweet_text += "#DirtierThanAverage "
    if actual_percent == previous_percent:
        tweet_text += "#SameAsBefore "
    elif actual_percent > previous_percent:
        tweet_text += "#CleanerThanBefore "
    else:
        tweet_text += "#DirtierThanBefore "
    if forecast_percent is not None:
        if actual_percent == forecast_percent:
            tweet_text += "#SameAsForecast "
        elif actual_percent > forecast_percent:
            tweet_text += "#CleanerThanForecast "
        else:
            tweet_text += "#DirtierThanForecast "

    # send tweet
    api = twitter.Api(consumer_key=TWITTER_CA_CONSUMER_KEY,
                      consumer_secret=TWITTER_CA_CONSUMER_SECRET,
                      access_token_key=TWITTER_CA_ACCESS_KEY,
                      access_token_secret=TWITTER_CA_ACCESS_SECRET)
    status = api.PostUpdate(tweet_text)
    return status

def update_bas(bas):
    # update and query BAs
    updates = []
    for ba in bas:
        try:
            result = BA_PARSERS[ba]().update()
        except Exception as e:
            result = "Update failed with error: %s" % e
        updates.append(result)
        
    # return
    return updates
