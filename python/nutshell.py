#!/bin/python3
# -*- coding: utf-8 -*-
"""
Product generator service appicable on the command line or as a library

Command line usage
==================

Basic format for all the command line invocations is::

    python3 -m nutshell.nutshell <nutshell-args>
    
Online help is optained with ``-h``::

    python3 -m nutshell.nutshell -h
    
Simple query using configuration file and product definition::

    python3 -m nutshell.nutshell -c nutshell/nutshell.cnf -m \\
      -p 201708121500_radar.rack.comp_SIZE=800,800_SITES=fiika_BBOX=20,62,30,70.png 





Using NutShell within python
============================

Simple example::

    import nutshell.nutshell as nuts

    # Initilize service
    server = nuts.ProductServer('nutshell/nutshell.cnf')

    # Retrieve / generate a product
    response = server.make_request("201708121600_radar.rack.comp_SITES=fikor,fivan,fiika_SIZE=800,800.png", "MAKE")

    # Results:
    # - error code returned by script (generate.sh)  
    print("Return code: {0} ".format(response.returncode))
    # -   
    print("Status (HTTP code): {0}: {0} ".format(response.status))
    print("File path: {0} ".format(response.returncode))

    # Further processing, image example
    from PIL import Image
    file = Image.open(response.path)
    print(file.info)


Status codes
============

NutShell recycles HTTP status codes in communicating success or failure
on operations. Especially:

=====  ============
Code   Enum Name
=====  ============
102    PROCESSING
200    OK
=====  ============

References:
- https://docs.python.org/3/library/http.html


"""

__version__ = '0.1'
__author__ = 'Markus.Peura@fmi.fi'

import os
import re
import subprocess # for shell escape

from pathlib import Path
from http import HTTPStatus
#import http.server
#HTTPresponses = http.server.SimpleHTTPRequestHandler.responses

import logging
logging.basicConfig(format='%(levelname)s\t %(name)s: %(message)s')
#logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
#logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p')
#logging.basicConfig(format='%(asctime)s %(levelname)s %(name)s : %(message)s', datefmt='%Y%m%d%H:%M:%S')

from . import nutils
from . import nutproduct




def parse_timestamp2(timestamp, result = {}):
    if (timestamp):
        t = re.sub("\W", "", timestamp)
        result['TIMESTAMP'] = t[0:12] # empty ok?
        result['YEAR']      = t[0:4]
        result['MONTH']     = t[4:6]
        result['DAY']       = t[6:8]
        result['HOUR']      = t[8:10]
        result['MINUTE']    = t[10:12]
    return result




ProductInfo = nutproduct.ProductInfo


class ProductServer:
    """Service designed for generating image and data products served as files 
    """

    PRODUCT_ROOT = '.'
    CACHE_ROOT = '.'
    TIME_DIR_SYNTAX = '{YEAR}/{MONTH}/{DAY}'
    SHELL_GENERATOR_SCRIPT = 'generate.sh'
    SHELL_INPUT_SCRIPT = 'input.sh'

    # HTTP Server Options (forward defs HTTP server, so perhaps later moved to NutServer )
    HTTP_PORT = 8088
    HTTP_NAME = ''
    HTTP_PATH_PREFIX = 'nutshell/' # TODO
    HTML_ROOT = '.' 
    HTML_TEMPLATE = 'template.html' 

    stdout = subprocess.PIPE
    stderr = subprocess.PIPE

    #verbosity = 5
    logger = None

    counter = 0

    #error_code_regexp = re.compile("^\s*([0-9]+)\\.(zip|gz)$")
   
    @classmethod
    def get_arg_parser(cls, parser = None):
        """Populates parser with options of this class"""

        parser = nutproduct.ProductInfo.get_arg_parser(parser)
        # parser = argparse.ArgumentParser()
 
        parser.add_argument("-c", "--conf", dest="CONF",
                            default=None, # "nutshell.cnf", #ProductServer.CONF_FILE?
                            help="read config file", 
                            metavar="<file>")
     
        parser.add_argument("-r", "--request", metavar="<string>",
                            dest="REQUEST",
                            default="",
                            help="comma-separated string of [DELETE|MAKE|INPUTS]")
    
        parser.add_argument("-d", "--delete",
                            dest="DELETE",
                            action="store_true",
                            #default=False,
                            help="delete product file, same as -r DELETE")
    
        parser.add_argument("-m", "--make",
                            dest="MAKE",
                            action="store_true",
                            #default=False,
                            help="make product, same as -r MAKE")
    
        parser.add_argument("-i", "--inputList",
                            dest="INPUTS",
                            action="store_true",
                            help="list input for a product, same as -r INPUTS")
    
        parser.add_argument("-D", "--directives",
                            dest="DIRECTIVES",
                            default='',
                            help="additional instructions: LOG,LINK,LATEST")
    
        return parser    

    
        
    class ProductRequest:
        """Container for storing information on requested product and server side resources derived thereof.
        """

        """Server assigned for manufacturing this product"""
        product_server = None

        """Specification of a product instance."""
        product_info = None

        """System-side directory containing script () for generating the product"""
        generator_path = ''

        """System-side full path to a dynamic directory and the generated product file."""
        path = ''
        
        """System-side full path to the generated file, the product."""
        path_static = ''        
        
        """Optional: System-side full path to the generated product file."""
        path_tmp = ''
     
        """Optional: Actual object (for example, python Image in the future) """
        product = None
        
        # Later, use (dir + file) object
        inputs = {}
        
        actions = []
        directives = []
        
        # Nutshell native log output
        log = None
        
        # Std output of the generation
        stdout = None
        
        # Error output of the generation
        stderr = None
        
        # Return code of the generation
        returncode = 0

        # Status, defined using HTTP status codes
        status = HTTPStatus.OK

        sid = 0
        
        builtin_directives = ("LOG", "LATEST", "LINK")        

        def set_status(self, status):
            """Set success or failure status using http.HTTPStatus codes.
            This is always logged.
            """
            self.log.debug(status)
            self.status = status
        
        def __init__(self, product_server, product_info, actions=None, directives=None, log=None):

            self.sid = (++ProductServer.counter)

            # Consider stripping 'product_'
            self.product_server = product_server

            if (type(product_info) == str):
                self.product_info = ProductInfo(product_info)
            else:        
                self.product_info = product_info

            if log:
                self.log = log
            else:
                self.log = logging.getLogger("ProductRequest")
 
            if (actions):
                self.actions = actions
            else:
                self.actions = []
            self.log.debug('actions:' + str(actions))
  
            if (directives):              
                self.directives = directives
            else:
                self.directives = []
            self.log.debug('directives: ' + str(self.directives))

            self.generator_path = Path(product_server.get_generator_dir(product_info), 
                                       product_server.SHELL_GENERATOR_SCRIPT)
            
            self.product = None
            self.inputs = {}
            #self.log = nutils.Log()
           
            filename        = product_info.filename()
            filename_latest = product_info.filename_latest()
            #cache_dir_dyn = product_server.get_dynamic_cache_dir(product_info)
            #cache_dir = product_server.get_cache_dir(product_info)
            #cache_root = product_server.CACHE_ROOT
            cache_root = Path(product_server.CACHE_ROOT)
            cache_root = str(cache_root.absolute())
            time_dir = product_server.get_time_dir(product_info)
            prod_dir = product_server.get_product_dir(product_info)
      
      
            # Target path. Will be cleared (to None) if product generation fails.
            self.path_relative = Path(time_dir, prod_dir, filename)
            self.path =        Path(cache_root, time_dir, prod_dir, filename)
            self.path_tmp =    Path(cache_root, time_dir, prod_dir, 'tmp', filename)  
            self.path_static = Path(cache_root, prod_dir, filename)
            self.path_latest = Path(cache_root, prod_dir, filename_latest)
            
            self.set_status( HTTPStatus.NO_CONTENT)  #204 # No content
            self.returncode = -1
            
            # self.path_tmp = self.cache_dir+os.sep+self.out_file_tmp
   
    class _InputInfo:
        
        inputs = None
        
        # Std output of the generation
        stdout = None
        
        # Error output of the generation
        stderr = None

        #
        log = None
        
        returncode = 0
        #def __init__(self, product_info):
        #    self.gdir = self.get_generator_dir(product_info)
        #    self.inputs = {}
        #    self.log = Log()
    
    def InputInfo(self, product_info, log = None):
         info = self._InputInfo() 

         info.script = Path(self.get_generator_dir(product_info),
                            self.SHELL_INPUT_SCRIPT)
         info.inputs = {}
         if (log):
             info.log = log
         else:
             info.log = logging.getLogger("InputInfo"+__name__)  #nutils.Log()
        
         info.returncode = 0
                
         return info

    
    def init_path(self, dirname, check_existence=False):
        """ Expand relative path to absolute, optionally check that exists. """ 
        #if (hasattr(self, dirname)):
        path = Path(getattr(self, dirname)).absolute()
        self.logger.warn('  {0} =>  {1}'.format(dirname, path))
        if (check_existence) and not (path.exists()):
            raise FileNotFoundError(__name__ + str(path))
        setattr(self, dirname, str(path)) # TODO -> Path obj
        #else:
        #     raise KeyError   
            
        
    def __init__(self, conffile = ''): 
        self.logger = logging.getLogger("NutShell2")
        if (conffile):
            self.read_conf(conffile)
        if __name__ == '__main__':
            self.stdout = os.sys.stdout # discarded
            self.stderr = os.sys.stderr
        #self._init_dir('PRODUCT_ROOT')
        #self._init_dir('CACHE_ROOT')
        # self._init_dir('HTML_ROOT') # not here!
        # self._init_dir('HTML_ROOT'+'/'+'HTML_TEMPLATE') # not here!
        
    def read_conf(self, conffile = 'nutshell.cnf', strict=True):
        if (os.path.exists(conffile)):
            self.logger.debug("reading conf file {0} ".format(conffile))
            result = nutils.read_conf(conffile)
            # print result
            nutils.set_entries(self, result)
        elif strict:
            self.logger.error("Conf file not found: " + conffile)
            raise FileNotFoundError("Conf file not found: ", conffile)
        else:
            self.logger.warning("Conf file not found: " + conffile)
            #print ("Conf file not found: ", conffile)  

    def get_status(self):  
        return nutils.get_entries(self)
    
    def get_product_dir(self, product_info):
        """Returns directory containing product generator script (generate.sh) and possible configurations etc"""
        return product_info.ID.replace('.', os.sep)
    
    def get_time_dir(self, timestamp):
        if (type(timestamp) != str):
            timestamp = timestamp.TIMESTAMP # product_info.TIMESTAMP
        if (timestamp):
            if (timestamp == 'LATEST'):
                return ''
            else:
                timevars = nutproduct.parse_timestamp(timestamp)
                # print timevars
                return self.TIME_DIR_SYNTAX.format(**timevars) # + os.sep
        else:
            return ''

    
    
    def get_generator_dir(self, product_info):
        path = Path(self.PRODUCT_ROOT, *product_info.ID.split('.'))
        return str(path.absolute())
        #return self.PRODUCT_ROOT+os.sep+product_info.ID.replace('.', os.sep)

   

    # Generalize?
    def ensure_output_dir(self, outdir):
        """Creates a writable directory, if non-existent
        """
        try:
            original_umask = os.umask(0)
            os.makedirs(str(outdir), 0o775, True)
        finally:
            os.umask(original_umask)
        return outdir


    def run_process(self, p, task, log):
        if (not p):
            log.warn('No process') 
            task.returncode = -1
            task.status = HTTPStatus.NOT_FOUND
            return

        stdout,stderr = p.communicate()
        task.returncode = p.returncode        

        if (stdout):
            stdout = stdout.decode(encoding='UTF-8')
            if (p.returncode != 0):
                lines = stdout.strip().split('\n')
                task.error_info = lines.pop()
                log.warn(task.error_info)
                try:             
                    status = int(task.error_info.split(' ')[0])
                    task.status = HTTPStatus(status)
                except ValueError:
                    log.warn('Not HTTP error code: {0} '.format(task.status))
                    task.status = HTTPStatus.CONFLICT
        if (stderr):
            stderr = stderr.decode(encoding='UTF-8')
        task.stdout = stdout  
        task.stderr = stderr
        
                   
    def get_input_list(self, product_info, directives, log):
        """ Used for reading dynamic input configuration generated by input.sh.
        directives determine how the product is generated. 
        """

        input_info = self.InputInfo(product_info)
        
        if (not input_info.script.exists()):
            log.debug("No input script: {0}".format(input_info.script))         
            return input_info   
        
        # TODO generalize (how)
        env = product_info.get_param_env()
        log.debug(env)
        
        p = subprocess.Popen(str(input_info.script),
                             cwd=str(input_info.script.parent),
                             stdout=subprocess.PIPE, # always
                             stderr=self.stderr, # stdout for cmd-line and subprocess.PIPE (separate) for http usage
                             shell=True,
                             env=env)

        self.run_process(p, input_info, log)  # log    

        if (input_info.returncode == 0): 
            #log.warning("inputsss")
            nutils.read_conf_text(input_info.stdout.split('\n'), input_info.inputs)
            log.info(input_info.inputs)
        else:
            log.warning("executing failed with error code={0}: {1} ".format(p.returncode, input_info.script))
            log.warning(input_info.error_info)
        #    else:
        #        log.critical("input script reported no error info")
                       
        return input_info
    

         
    def run_generator(self, product_request, params=None):
        """ Run shell script to generate a product. 
        
         stdout and stderr are used for output.
        
        Attributes:
          product_request -- state at beginning of transition.
          params -- attempted new state.
        """

        if (params == None):
            params = {}

        product_request.log.info('run_generator: ' + product_request.product_info.ID)
        product_request.log.debug(params)
    
        p = subprocess.Popen(str(product_request.generator_path), #'./'+self.SHELL_GENERATOR_SCRIPT,
                             cwd=str(product_request.generator_path.parent),
                             stdout=subprocess.PIPE,  #self.stdout,
                             stderr=subprocess.STDOUT, # Use same stream as stdout, be it os.stdout or subprocess.STDOUT
                             shell=True,
                             env=params)
              
        self.run_process(p, product_request, product_request.log)              
        if (p.returncode != 0):
            if (product_request.stdout):
                log_file = Path(str(product_request.path)+'.stdout.log')
                product_request.log.warn('Writing STDOUT log: {0}'.format(log_file))            
                log_file.write_text(product_request.stdout)
            if (product_request.stderr):
                log_file = Path(str(product_request.path)+'.stderr.log')
                product_request.log.warn('Writing STDERR log: {0}'.format(log_file))            
                log_file.write_text(product_request.stderr)
            
        return p.returncode


    def make_request(self, product_info, actions = ['MAKE'], directives = None, log = None):
        """" Return path or log
        'MAKE'   - return the product, if in cache, else generate it and return
        'DELETE' - delete the product in cache
        'INPUTS' - generate and store the product, also regenerate even if already exists
        """
        product_request = self.ProductRequest(self, product_info, actions, directives, log)
        return self.handle_request(product_request)
        
        
    def handle_request(self, pr):
        """" Return path or log
        'MAKE'   - return the product, if in cache, else generate it and return
        'DELETE' - delete the product in cache
        'INPUTS' - generate and store the product, also regenerate even if already exists
        """
        
        if (pr.path.exists()):  
            pr.log.debug('File exists: {0}'.format(pr.path))
            if ('DELETE' in pr.actions):
                pr.log.info('Deleting...')
                pr.path.unlink()
                pr.set_status(HTTPStatus.ACCEPTED)  #202 # Accepted
            elif ('MAKE' in pr.actions): # PATH_ONLY
                if (pr.path.stat().st_size > 0):
                    pr.product = pr.path
                    pr.log.info('Non-empty result file found: ' + str(pr.path))
                    pr.set_status(HTTPStatus.OK)
                else:
                    pr.product = '' # BUSY
                    pr.log.warning('BUSY') # TODO riase (prevent deletion)
                    pr.set_status(HTTPStatus.ACCEPTED)  #202 # Accepted
                return pr
        else:
            pr.log.debug('File not found: {0}'.format(pr.path))

        # only check at this point
        #if (os.path.exists(pr.generator_script)):
        if (pr.generator_path.exists()):
            pr.log.debug('Generator script ok: {0}'.format(pr.generator_path))
        else:
            pr.log.warning('Generator script not found: {0}'.format(pr.generator_path))
            # Consider case of copied valid product (without local generator)            
            pr.path = ''
            pr.set_status(HTTPStatus.NOT_IMPLEMENTED) # Not Implemented
            return pr
        

        # TODO: if not stream?
        params = {}
        if ('MAKE' in pr.actions):
            pr.log.debug('Ensuring cache dir for: {0}'.format(pr.path))
            self.ensure_output_dir(pr.path_tmp.parent)
            self.ensure_output_dir(pr.path.parent)

            # what about true ENV?
            params = pr.product_info.get_param_env() # {})
            params['OUTDIR']  = str(pr.path_tmp.parent)
            params['OUTFILE'] = pr.path.name
            #os.mknod(pr.path) # = touch
            pr.path.touch()
            
        # Runs input.sh
        if ('MAKE' in pr.actions) or ('INPUTS' in pr.actions):
            input_info = self.get_input_list(pr.product_info, pr.directives, pr.log.getChild('get_input_list'))
            if (input_info.returncode == 0):
                pr.inputs = input_info.inputs
            else:
                #         pr.log.warn('Not HTTP error code: {0} '.format(status))
                pr.set_status(HTTPStatus.CONFLICT)
                pr.log.info('Removing: {0} '.format(pr.path))
                pr.path.unlink()
                return pr

        if ('MAKE' in pr.actions): 
            pr.log.debug('Retrieving inputs for: ' + str(pr.path.name))
            inputs = {}
            for i in pr.inputs:
                #pr.log.info('INPUTFILE: ' + i)
                input = pr.inputs[i] # <filename>.h5
                input_prod_info = nutproduct.ProductInfo(input)
                pr.log.info('Make input: {0} ({1})'.format(i, input_prod_info.ID))
                r = self.make_request(input_prod_info, ['MAKE'], [], pr.log.getChild("input[{0}]".format(i)))
                if (r.path):
                    inputs[i] = str(r.path) # sensitive
                    pr.log.debug('Success: ' + str(r.path))
                else:
                    pr.log.warning('SKIPPED: ' + i) 
            # warn if  none succeeded?
            pr.inputs = inputs
            #pr.log.extend2(pr.inputs , 'INPUTPATH: ')
            params['INPUTKEYS'] = ','.join(pr.inputs.keys())
            params.update(pr.inputs)

        # MAIN
        if ('MAKE' in pr.actions):
            pr.log.info('Generating: {0}'.format(pr.path))
            self.run_generator(pr, params)

            if (pr.returncode != 0):
                pr.log.error("generator failed")
                return pr
                
            if (pr.path_tmp.stat().st_size == 0):
                pr.log.error("generator failed")
                return pr
            
            pr.log.debug("Final move from tmp")
            pr.path_tmp.replace(pr.path)
            pr.set_status(HTTPStatus.OK)
                
            try:
                if ('LINK' in pr.directives): #and pr.product_info.TIMESTAMP:
                    pr.log.info('LINK: {0} '.format(pr.path_static))
                    self.ensure_output_dir(pr.path_static.parent)
                    nutils.symlink(pr.path_static, pr.path)
         
                if ('LATEST' in pr.directives):
                    pr.log.info('LATEST: {0} '.format(pr.path_latest))
                    self.ensure_output_dir(pr.path_latest.parent)
                    nutils.symlink(pr.path_latest, pr.path, True)
            except:
                 pr.log.warn("Linking file failed")               
 
            if ('DEBUG' in pr.directives) or ('LOG' in pr.directives):
                logfile = Path(str(pr.path) + '.log')
                pr.log.info('Saving log: {0} '.format(logfile))
                try:
                    logfile.write_text(pr.stdout) #.decode(encoding='UTF-8'))            
                except:
                    pr.log.warn("Saving log failed")               
                
        return pr
    




        
if __name__ == '__main__':

    product_server = ProductServer()

    #print()
    #logger = logging.getLogger(__name__ + str(++product_server.counter))
    logger = logging.getLogger('NutShell')
    logger.setLevel(30)
    #logger.debug("parsing arguments")
    
    parser = ProductServer.get_arg_parser() # ProductInfo.get_arg_parser(parser)
    
    #(options, args) = parser.parse_args()
    options = parser.parse_args()
    #logger.warning(*options.__dict__)  
    
    if (not options):
        parser.print_help()
        exit(1)
    
    if (options.VERBOSE):
        options.LOG_LEVEL = "DEBUG"
        
    if (options.LOG_LEVEL):
        if hasattr(logging, options.LOG_LEVEL):
            #nutils.VERBOSITY_LEVELS[options.VERBOSE]
            logger.setLevel(getattr(logging, options.LOG_LEVEL))
        else:
            logger.setLevel(int(options.LOG_LEVEL))
    
    logger.debug(options)   
    
    product_server.verbosity = int(options.VERBOSE)
    product_server.logger = logger # NEW
    product_info = nutproduct.ProductInfo()

    if (options.PRODUCT):
        product_info.parse_filename(options.PRODUCT)
    else:
        logger.warning("product not defined")
    
        

    if (options.CONF):
        product_server.read_conf(options.CONF)

    #if (options.VERBOSE > 4):
    #nutils.print_dict(product_server.get_status())
    logger.debug(product_server.get_status())
     
    request = []
    
    if (options.INPUTS):
        request.append('INPUTS')

    if (options.REQUEST):
        request.append(options.REQUEST.split(','))
        
    if (options.DELETE):
        request.append('DELETE')


    if (options.MAKE) or not (request):
        request.append('MAKE')

    if (product_info.ID and product_info.FORMAT):
        
        logger.info('Requests: {0}'.format(str(request)))

        directives = []
        if (options.DIRECTIVES):
            #directives = nutils.read_conf_text(options.DIRECTIVES.split(',')) # whattabout comma in arg?
            directives = options.DIRECTIVES.split(',') # whattabout comma in arg?

        product_request = product_server.make_request(product_info, request, directives, logger.getChild("make_request"))
        #logger.debug(product_request)

        if ('INPUTS' in request): # or (options.VERBOSE > 6):
            #nutils.print_dict(product_request.inputs)
            logger.info(product_request.inputs)

        #        if (product_request.status.value >= 500):
        #            print (product_request.stdout)
        #            print (product_request.stderr)
        #            logger.critical(product_request.status)
        #            exit(5)
        #        elif (product_request.status.value >= 400):
        #            print (product_request.stdout)
        #            print (product_request.stderr)
        #            logger.error(product_request.status)
        #            exit(4)
        #        elif (product_request.status.value >= 300):
        #            print (product_request.stdout)
        #            print (product_request.stderr)
        #            logger.warning(product_request.status)
        #            exit(3)
        #        else:            
        logger.info(product_request.status)    
            
            
    else:
        logger.warning('Could not parse product')
        exit(1)
        #print('Could not parse product')
        
    exit(0)
