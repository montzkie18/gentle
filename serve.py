import multiprocessing
import requests
import logging
import shutil
import gentle
import uuid
import json
import os

from twisted.web.static import File
from twisted.web.resource import Resource
from twisted.web.server import Site, NOT_DONE_YET
from twisted.internet import reactor, threads

from gentle.util.paths import get_datadir

AUDIO_FILENAME='audio.wav'
TEXT_FILENAME='transcript.txt'
RESAMPLE_FILENAME='audio-resampled.wav'

class Utils():
    @staticmethod
    def get_next_uid(data_dir):
        uid = None
        while uid is None or os.path.exists(os.path.join(data_dir, uid)):
            uid = uuid.uuid4().hex[:8]
        return uid

    @staticmethod
    def create_output_dir(data_dir, uid):
        output_dir = os.path.join(data_dir, 'transcriptions', uid)
        os.makedirs(output_dir)
        return output_dir

    @staticmethod
    def remove_directory(output_dir):
        try:
            logging.info('Cleaning up %s', output_dir)
            if os.path.exists(output_dir) and os.path.isdir(output_dir):
                shutil.rmtree(output_dir)
        except Exception as e:
            logging.info('Failed to clean up %s: %s', output_dir, e)

    @staticmethod
    def download_url_to_path(url, path):
        try:
            logging.info('Trying to download %s', url)
            with open(path, 'wb') as file:
                with requests.get(url, stream=True) as response:
                    response.raise_for_status()
                    file.writelines(response.iter_content(1024))
            logging.info('Download success for %s', url)
            return True
        except Exception as e:
            logging.info('Download failed for %s: %s', url, e)
            return False

class Transcriber():
    def __init__(self, nthreads=4, ntranscriptionthreads=2):
        self.nthreads = nthreads
        self.ntranscriptionthreads = ntranscriptionthreads
        self.resources = gentle.Resources()

    def transcribe(self, output_dir, **kwargs):
        orig_audio = os.path.join(output_dir, AUDIO_FILENAME)
        resample_audio = os.path.join(output_dir, RESAMPLE_FILENAME)
        transcript = os.path.join(output_dir, TEXT_FILENAME)

        logging.info('Resampling audio file %s', orig_audio)
        if gentle.resample(orig_audio, resample_audio) != 0:
            logging.info('Failed to resample %s', orig_audio)
            return -1

        def on_progress(p):
            for k,v in p.items():
                logging.info('Transcribing %s, %s, %s', resample_audio, k, v)

        logging.info('Starting to transcribe %s', output_dir)
        transcriber = gentle.ForcedAligner(self.resources, transcript, nthreads=self.nthreads, **kwargs)
        output = transcriber.transcribe(resample_audio, progress_cb=on_progress, logging=logging)
        logging.info('Finished transcribing %s', output_dir)
        return output


class TranscriptionsController(Resource):
    def __init__(self, data_dir, transcriber, webhook_url=None):
        self.data_dir = data_dir
        self.transcriber = transcriber
        self.webhook_url = webhook_url

    def render_POST(self, req):
        logging.info('Transcription request: %s', req.content.getvalue())

        # parse JSON body
        params = json.loads(req.content.getvalue())
        audio_url = params['audioUrl']
        transcript_url = params['transcriptUrl']
        metadata = params['metadata']

        # some args are passed as URL params
        disfluency = True if b'disfluency' in req.args else False
        conservative = True if b'conservative' in req.args else False
        is_async = True if b'async' not in req.args or req.args[b'async'][0] != b'false' else False

        uid = Utils.get_next_uid(self.data_dir)
        output_dir = Utils.create_output_dir(self.data_dir, uid)

        audio_path = os.path.join(output_dir, AUDIO_FILENAME)
        if not Utils.download_url_to_path(audio_url, audio_path):
            Utils.remove_directory(output_dir)
            req.setResponseCode(404)
            return json.dumps({'message': 'Can\'t download audio from {}'.format(audio_url)}).encode('utf-8')

        transcript_path = os.path.join(output_dir, TEXT_FILENAME)
        if not Utils.download_url_to_path(transcript_url, transcript_path):
            Utils.remove_directory(output_dir)
            req.setResponseCode(404)
            return json.dumps({'message': 'Can\'t download transcript from {}'.format(transcript_url)}).encode('utf-8')

        kwargs = {'disfluency': disfluency,
                  'conservative': conservative,
                  'disfluencies': set(['uh', 'um'])}

        result_promise = threads.deferToThreadPool(
            reactor, reactor.getThreadPool(),
            self.transcriber.transcribe,
            output_dir, **kwargs)

        def write_result(result):
            '''Write JSON to client on completion'''
            logging.info('Sending result of %s to caller', uid)
            req.setHeader("Content-Type", "application/json")
            req.write(result.to_json(indent=2).encode('utf-8'))
            req.finish()
            return result

        def cleanup_outdir(result):
            Utils.remove_directory(output_dir)
            return result

        def send_result_to_webhooks(result):
            if self.webhook_url:                
                response = -1 if not hasattr(result, 'to_json') else result.to_json(indent=2)
                message_type = 'realign_success' if response is not -1 else 'realign_fail'
                payload = {'type': message_type,
                           'transcription_id': uid,
                           'metadata': metadata,
                           'result': response,
                           'status': 'DONE'}
                
                logging.info('Sending result of %s to %s', uid, self.webhook_url)
                try:
                    with requests.post(self.webhook_url, json=payload) as response:
                        response.raise_for_status()
                    logging.info('Webhook sent')
                except Exception as e:
                    logging.info('Sending webhook failed %s', e)
            return result

        def handle_error(error):
            logging.info('Failed during transcription %s: %s', uid, error)

        def cancel_transcription(error):
            logging.info('Cancelled transcription %s: %s', uid, error)
            result_promise.cancel()
            cleanup_outdir(None)

        if not is_async: result_promise.addCallback(write_result)
        result_promise.addCallback(cleanup_outdir)
        result_promise.addCallback(send_result_to_webhooks)
        result_promise.addErrback(handle_error)

        if is_async:
            req.setHeader("Content-Type", "application/json")
            return json.dumps({'transcription_id': uid, 'status': 'IN_PROGRESS'}).encode('utf-8')
        else:
            req.notifyFinish().addErrback(cancel_transcription)
            return NOT_DONE_YET


def serve(args):
    logging.info('SERVE %d, %s', args.port, args.host)

    data_dir = get_datadir('webdata')
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)

    transcriber = Transcriber(nthreads=args.nthreads, ntranscriptionthreads=args.ntranscriptionthreads)
    controller = TranscriptionsController(data_dir, transcriber, webhook_url=args.webhook)

    file = File(data_dir)
    file.putChild(b'transcriptions', controller)
    site = Site(file)

    logging.info('about to listen')
    reactor.listenTCP(args.port, site, interface=args.host)
    logging.info('listening')

    reactor.run(installSignalHandlers=1)

if __name__=='__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Align a transcript to audio by generating a new language model.')
    parser.add_argument('--host', default="0.0.0.0",
                       help='host to run http server on')
    parser.add_argument('--port', default=8765, type=int,
                        help='port number to run http server on')
    parser.add_argument('--nthreads', default=multiprocessing.cpu_count(), type=int,
                        help='number of alignment threads')
    parser.add_argument('--ntranscriptionthreads', default=2, type=int,
                        help='number of full-transcription threads (memory intensive)')
    parser.add_argument('--log', default="INFO",
                        help='the log level (DEBUG, INFO, WARNING, ERROR, or CRITICAL)')
    parser.add_argument('--webhook', default=None,
                        help='URL which we send a POST request to after successfully transcribing a file')

    args = parser.parse_args()

    log_level = args.log.upper()
    logging.getLogger().setLevel(log_level)

    logging.info('gentle %s' % (gentle.__version__))
    logging.info('listening at %s:%d\n' % (args.host, args.port))

    serve(args)
