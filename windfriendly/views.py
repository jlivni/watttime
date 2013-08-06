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
# Authors: Josh Livni, Sam Marcellus, Anna Schneider, Kevin Yang


from datetime import datetime, timedelta
from dateutil import tz
import pytz
import json
import logging

from django.core.exceptions import FieldError
from django.http import HttpResponse

from windfriendly.models import User, MeterReading
from windfriendly.parsers import GreenButtonParser
from windfriendly.balancing_authorities import BALANCING_AUTHORITIES, BA_MODELS, BA_PARSERS
import windfriendly.utils as windutils

def json_response(func):
  """
  A decorator thats takes a view response and turns it
  into json. If a callback is added through GET or POST
  the response is JSONP.
  """
  def decorator(request, *args, **kwargs):
    objects = func(request, *args, **kwargs)
    if isinstance(objects, HttpResponse):
      return objects
    try:
      data = json.dumps(objects)
      if 'callback' in request.REQUEST:
        # a jsonp response!
        data = '%s(%s);' % (request.REQUEST['callback'], data)
        return HttpResponse(data, "text/javascript")
    except:
        data = json.dumps(str(objects))
    return HttpResponse(data, "application/json")
  return decorator

def ba_from_request(request):
    """
    Given a GET request with location info, return balancing authority
       name and model queryset.
    Location info can be st (state), or ba (balancing authority).
    Future support for lat+lng, zipcode, country code, etc.
    Returns tuple of (string, QuerySet)
    """
    # try BA
    ba = request.GET.get('ba', None)
    if ba:
      ba = ba.upper()
      return ba, BA_MODELS[ba].objects.all()

    # try state
    state = request.GET.get('st', None)
    if state:
      state = state.upper()
      ba = BALANCING_AUTHORITIES[state]
      return ba, BA_MODELS[ba].objects.all()

    # got nothing
    logging.debug('returning null BA')
    return None, None
    
def utctimes_from_request(request):
    # get requested date range, if any
    start = request.GET.get('start', None)
    end = request.GET.get('end', None)
    tz_offset = request.GET.get('tz', 0)

    # set up actual start and end times (default is -Inf to now)
    if start:
        utc_start = datetime.strptime(start, '%Y%m%d%H%M').replace(tzinfo=pytz.utc)
        utc_start += timedelta(hours = int(tz_offset))
    else:
        utc_start = datetime.min.replace(tzinfo=pytz.utc)
    if end:
        utc_end = datetime.strptime(end, '%Y%m%d%H%M').replace(tzinfo=pytz.utc)
        utc_end += timedelta(hours = int(tz_offset))
    else:
        utc_end = datetime.utcnow().replace(tzinfo=pytz.utc)
        
    return utc_start, utc_end

@json_response
def update(request, utility):
    # try to get info from request
    try:
        file = request.GET.get('file', None)
        uid = request.GET.get('uid', None)
        name = request.GET.get('name', 'New User')

    # ok if passed without request
    except:
        file = None
        uid = None
        name = None

    # update utility
    ba = utility.upper()
    if ba in BA_PARSERS:
        parser = BA_PARSERS[ba]()
    elif utility == 'gb':
        if uid is None:
            uid = User.objects.create(name=name).pk
        parser = GreenButtonParser(file, uid)
    else:
        raise ValueError("No update instructions found for %s" % utility)

    # return
    return parser.update()

@json_response
def averageday(request):
    # get name and queryset for BA
    ba_name, ba_qset = ba_from_request(request)
    # if no BA, error
    if ba_name is None:
        raise ValueError("No balancing authority found, check location arguments.")

    # get requested date range
    utc_start, utc_end = utctimes_from_request(request)

    # get rows
    try:
        ba_rows = ba_qset.filter(date__range=(utc_start, utc_end), forecast_code=0)
    except FieldError:
        ba_rows = ba_qset.filter(date__range=(utc_start, utc_end))
        
    if ba_rows.count() == 0:
        print 'no data for UTC start %s, end %s' % (repr(utc_start), repr(utc_end))
        return []
    
    # collect data
    data = []
    for hour in range(24):
        group = ba_rows.filter_by_hour(hour)
        if group.count() > 0:
            # get average data
            total_green = 0
            total_dirty = 0
            total_load = 0
            for r in group:
                total_green += r.fraction_green
                total_dirty += r.fraction_high_carbon
                total_load += r.total_load
            average_green = round(total_green*100/group.count(), 3)
            average_dirty = round(total_dirty*100/group.count(), 3)
            average_load = total_load/group.count()
            representative_date = group.latest().local_date.replace(minute=0)
        else:
            # get null data
            average_green = None
            average_dirty = None
            average_load = None
            representative_date = ba_qset.latest().local_date.replace(hour=hour, minute=0)
        
        # complicated date wrangling to get all local_time values in local today
        utcnow = datetime.utcnow().replace(tzinfo=pytz.utc)
        latest_day = utcnow.astimezone(BA_MODELS[ba_name].TIMEZONE).day
        local_time = representative_date.replace(day=latest_day)
        utc_time = local_time.astimezone(pytz.utc)
        
        # add to list
        data.append({'percent_green': average_green,
                     'percent_dirty': average_dirty,
                     'load_MW': average_load,
                     'utc_time': utc_time.strftime('%Y-%m-%d %H:%M'),
                     'local_time': local_time.strftime('%Y-%m-%d %H:%M'),
                     'hour': local_time.hour,
                    })

    # return
    return sorted(data, key=lambda r: r['local_time'])

@json_response
def today(request):
    """Get best data from today (actual until now, best forecast for future)"""
    # get name and queryset for BA
    ba_name, ba_qset = ba_from_request(request)
    # if no BA, error
    if ba_name is None:
        raise ValueError("No balancing authority found, check location arguments.")

    # get date range
    ba_local_now = datetime.now(BA_MODELS[ba_name].TIMEZONE)
    ba_local_start = ba_local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    ba_local_end = ba_local_start + timedelta(1) - timedelta(0, 1)
    utc_start = ba_local_start.astimezone(pytz.utc)
    utc_end = ba_local_end.astimezone(pytz.utc)

    # get rows
    ba_rows = ba_qset.filter(date__range=(utc_start, utc_end)).best_guess_points()
    if len(ba_rows) == 0:
        print 'no data for local start %s, end %s' % (repr(ba_local_start), repr(ba_local_end))
        return []
    
    # collect data
    data = [r.to_dict() for r in ba_rows]

    # return
    return data
          
@json_response
def alerts(request):
    # get name and queryset for BA
    ba_name, ba_qset = ba_from_request(request)
    # if no BA, error
    if ba_name is None:
        raise ValueError("No balancing authority found, check location arguments.")

    # set up actual start and end times (default is today in BA local time)
    utc_start, utc_end = utctimes_from_request(request)
    if not utc_start:
        ba_local_now = datetime.now(BA_MODELS[ba_name].TIMEZONE)
        ba_local_start = ba_local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        utc_start = ba_local_start.astimezone(pytz.utc)
    if not utc_end:
        ba_local_now = datetime.now(BA_MODELS[ba_name].TIMEZONE)
        ba_local_start = ba_local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        ba_local_end = ba_local_start + timedelta(1) - timedelta(0, 1)
        utc_end = ba_local_end.astimezone(pytz.utc)

    # get best guess data
    ba_rows = ba_qset.filter(date__range=(utc_start, utc_end)).best_guess_points()
    
    # set up storage
    if len(ba_rows) > 0:
        data = {}
    else:
        return {}

    # get notable times
    sorted_green = sorted(ba_rows, key=lambda r : r.fraction_green, reverse=True)
    data['highest_green'] = sorted_green[0].to_dict()
    sorted_dirty = sorted(ba_rows, key=lambda r : r.fraction_high_carbon, reverse=True)
    data['highest_dirty'] = sorted_dirty[0].to_dict()
    sorted_load = sorted(ba_rows, key=lambda r : r.total_load, reverse=True)
    data['highest_load'] = sorted_load[0].to_dict()
    data['lowest_load'] = sorted_load[-1].to_dict()
    sorted_marginal = sorted(ba_rows, key=lambda r : r.marginal_fuel)
    data['worst_marginal'] = sorted_marginal[0].to_dict()
   # data['best_marginal'] = sorted_marginal[-1].to_dict() TODO: use best non-None marginal
    
    # return
    return data

@json_response
def average_usage_for_period(request, userid):
    # TODO untested and probably broken!!! check date handling

    # get name and queryset for BA
    ba_name, ba_qset = ba_from_request(request)
    # if no BA, error
    if ba_name is None:
        raise ValueError("No balancing authority found, check location arguments.")

    # get grouping to return
    grouping = request.GET.get('grouping')
    groupings = grouping.split(',')

    # get time info
    start = request.GET.get('start', '')
    if start:
      starttime = datetime.strptime(start, '%Y%m%d%H%M').replace(tzinfo=tz.tzlocal())
    else:
      starttime = datetime.min.replace(tzinfo=tz.tzlocal())
    end = request.GET.get('end', '')
    if end:
      endtime = datetime.strptime(end, '%Y%m%d%H%M').replace(tzinfo=tz.tzlocal())
    else:
      endtime = datetime.utcnow().replace(tzinfo=tz.tzlocal())

    # get user data
    user_rows = MeterReading.objects.filter(start__gte=starttime,
                                            start__lt=endtime,
                                            userid__exact=userid)
    if user_rows.count() == 0:
      raise ValueError('no data')

    # set up grouping functions
    hour_bucket = lambda row : row.start.astimezone(tz.tzlocal()).hour
    if not grouping:
      bucket = lambda row : 'All'
    elif grouping == 'hour':
      bucket = hour_bucket
    elif grouping == 'month':
      bucket = lambda row : row.start.strftime('%B')
    elif grouping == 'day':
      bucket = lambda row : row.start.strftime('%A')
    elif grouping == 'weekdays':
      bucket = lambda row : 'weekends' if row.start.weekday() in [0,6] else 'weekdays'

    # put rows in buckets
    buckets = {}
    for row in user_rows:
      if bucket(row) in buckets:
        if hour_bucket(row) in buckets[bucket(row)]:
          buckets[bucket(row)][hour_bucket(row)].append(row)
        else:
          buckets[bucket(row)][hour_bucket(row)] = [row]
      else:
        buckets[bucket(row)] = {}
        buckets[bucket(row)][hour_bucket(row)] = [row]

    # collect grouped and total results
    results = {}
    total_green_kwh = 0
    total_kwhs = 0
    for key,group in buckets.iteritems():
      results[key] = {}
      for subkey, subgroup in group.iteritems():
        # compute kwh
        subgroup_green_kwh = sum([windutils.used_green_kwh(row, ba_qset) for row in subgroup])
        subgroup_total_kwh = sum([windutils.total_kwh(row, ba_qset) for row in subgroup])

        # store in results
        results[key][subkey] = {}
        results[key][subkey]['total_green_kwh'] = subgroup_green_kwh
        results[key][subkey]['total_kwhs'] = subgroup_total_kwh
        results[key][subkey]['percent_green'] = subgroup_green_kwh / subgroup_total_kwh * 100.0
        results[key][subkey]['total_cost'] = row.total_cost()

        # store in totals
        total_green_kwh += subgroup_green_kwh
        total_kwhs += subgroup_total_kwh

    # get overall percent green
    percent_green = total_green_kwh / total_kwhs * 100.0
    
    # return data
    data = {
      'userid': userid,
      'ba': ba_name,
      'percent_green': round(percent_green,3),
      'total_kwh': total_kwhs,
      'buckets': results
      }
    return data
  

